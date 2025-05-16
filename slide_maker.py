# -*- coding: utf-8 -*-
"""Slide Maker.ipynb"""

from base64 import b64decode
from io import BytesIO
from pydub import AudioSegment

import numpy as np
import whisper
import soundfile as sf
from openai import OpenAI
import openai
import anthropic

import os, getpass
import tempfile
from pydub import AudioSegment
from dotenv import load_dotenv

from google.oauth2 import service_account
from googleapiclient.discovery import build
import re
import json

import pyaudio
import wave
import time

import logging
import tempfile

# Load environment variables
load_dotenv()


def _set_env(var: str):
  if not os.environ.get(var):
    os.environ[var] = getpass.getpass(f"{var}: ")


_set_env("OPENAI_API_KEY")

# Grant access to tools
SCOPES = [
    'https://www.googleapis.com/auth/presentations',
    'https://www.googleapis.com/auth/drive'
]

# Load credentials from service account info
try:
    service_account_json = os.getenv('SERVICE_ACCOUNT_PATH')
    if service_account_json:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_file:
            temp_file.write(service_account_json)
            temp_file.flush()
            credentials = service_account.Credentials.from_service_account_file(
                temp_file.name, scopes=SCOPES)
            os.unlink(temp_file.name)
    else:
        logging.error("SERVICE_ACCOUNT_PATH environment variable not found")
        credentials = None
except Exception as e:
    logging.error(f"Error loading credentials: {e}")
    credentials = None


def record_until_silence(threshold=30, silence_duration=4):
  """Record audio until silence is detected."""
  CHUNK = 1024
  FORMAT = pyaudio.paFloat32
  CHANNELS = 1
  RATE = 44100

  p = pyaudio.PyAudio()
  stream = p.open(format=FORMAT,
                  channels=CHANNELS,
                  rate=RATE,
                  input=True,
                  frames_per_buffer=CHUNK)

  print("Recording...")

  frames = []
  silence_start = None

  try:
    while True:
      data = stream.read(CHUNK)
      frames.append(data)
      audio_data = np.frombuffer(data, dtype=np.float32)
      volume_norm = np.linalg.norm(audio_data) * 10

      if volume_norm < threshold:
        if silence_start is None:
          silence_start = time.time()
        elif time.time() - silence_start > silence_duration:
          break
      else:
        silence_start = None

  except KeyboardInterrupt:
    pass

  print("Recording stopped")

  stream.stop_stream()
  stream.close()
  p.terminate()

  # Save as WAV file
  with tempfile.NamedTemporaryFile(suffix=".wav",
                                   delete=False) as tmp_wav_file:
    wf = wave.open(tmp_wav_file.name, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(b''.join(frames))
    wf.close()
    return tmp_wav_file.name


def convert_audio_segment_to_wav(audio_segment, sample_rate=16000):
  # Set the sample rate if it's different from the original
  if audio_segment.frame_rate != sample_rate:
    audio_segment = audio_segment.set_frame_rate(sample_rate)

  # Create a temporary WAV file
  with tempfile.NamedTemporaryFile(suffix=".wav",
                                   delete=False) as tmp_wav_file:
    # Export audio directly to the WAV file
    audio_segment.export(tmp_wav_file.name, format="wav")
    wav_path = tmp_wav_file.name

  return wav_path


def transcribe_audio(wav_buffer):
  # Initialize OpenAI client
  client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

  # Transcribe audio using OpenAI API
  with open(wav_buffer, "rb") as wav_file:
    try:
      response = client.audio.transcriptions.create(model="whisper-1",
                                                    file=wav_file,
                                                    response_format="text")
      print(response)
      return response
    except Exception as e:
      print(f"An error occurred: {e}")
      raise Exception(f"Transcription failed: {str(e)}")


def generate_code_from_instructions(instructions_text):
  # Initialize Anthropic Client
  code_client = anthropic.Anthropic(api_key=os.getenv('CLAUDE_API_KEY'), )

  # Generate completion
  response = code_client.messages.create(model="claude-3-7-sonnet-20250219",
                                         max_tokens=20000,
                                         temperature=1,
                                         system="""
                          You are an engineer, create a list of requests in python code that makes the content of a
                          Google slides presentation from the human instructions.
                          The code will be used as content for requests in another function where we call the Google API so in your response start immediately with the code like this: ['{
                          'createSlide':'. Do not include the 'request = []', or any text, like '''json, just the list.
                          Please format the output as valid JSON
                          with double quotes for all property names and string values.
                          Every item in the request list should be formatted as a dictionary of dictionaries, like this {{}}.
                          Here is an example of a request item for createSlide: {
                            "createSlide": {
                                "objectId": f"slide_{len(requests)}",
                                "slideLayoutReference": {
                                    "predefinedLayout": slide_info.get("slideType", "BLANK")
                                }
                            }
                        } Thank you!""",
                                         messages=[{
                                             "role":
                                             "user",
                                             "content": [{
                                                 "type":
                                                 "text",
                                                 "text":
                                                 f"{instructions_text}"
                                             }]
                                         }])

  generated_code = response.content[0].text
  cleaned_result = re.sub(r'^```python\n|```$',
                          '',
                          generated_code,
                          flags=re.MULTILINE)
  return cleaned_result.strip()


def create_presentation(credentials):
  # Build the service
  service = build('slides', 'v1', credentials=credentials)

  # Create a presentation
  presentation = service.presentations().create(body={
      'title': 'Sample Presentation'
  }).execute()
  presentation_id = presentation['presentationId']
  return service, presentation_id


def run_generated_code(generated_code, presentation_id, service):
  try:
    requests = json.loads(generated_code)
  except json.JSONDecodeError as e:
    return "", {"json_error": str(e)}

  errors = {}
  fixed_requests = []
  
  # First attempt to run all requests
  for index, req in enumerate(requests):
    try:
      service.presentations().batchUpdate(presentationId=presentation_id,
                                        body={'requests': [req]}).execute()
      fixed_requests.append(req)
    except Exception as e:
      errors[str(req)] = str(e)

  # If there are errors, try to fix each failed request
  if errors:
    errors_to_fix = dict(errors)  # Create a copy of the errors dictionary
    for failed_req, error in errors_to_fix.items():
      fix_prompt = f"This code section failed: {failed_req} with error: {error}. Please fix only this specific section while maintaining the same functionality."
      fixed_code = generate_code_from_instructions(fix_prompt)
      
      try:
        fixed_json = json.loads(fixed_code)
        if isinstance(fixed_json, list):
          fixed_req = fixed_json[0]  # Take first request if multiple returned
        else:
          fixed_req = fixed_json
          
        service.presentations().batchUpdate(presentationId=presentation_id,
                                          body={'requests': [fixed_req]}).execute()
        fixed_requests.append(fixed_req)
        errors.pop(str(failed_req), None)
      except Exception as e:
        errors[str(failed_req)] = f"Original and fix attempt failed: {str(e)}"

  url = f'https://docs.google.com/presentation/d/{presentation_id}/edit'
  return url, errors


def share_presentation(presentation_id, email, credentials):
  drive_service = build('drive', 'v3', credentials=credentials)
  drive_service.permissions().create(fileId=f'{presentation_id}',
                                     body={
                                         'type': 'user',
                                         'role': 'writer',
                                         'emailAddress': f'{email}'
                                     },
                                     fields='id').execute()
