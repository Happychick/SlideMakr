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

# Load credentials from service account file
credentials = service_account.Credentials.from_service_account_file(
    'slidemakr-ac7d7a834a05.json', scopes=SCOPES)


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
  try:
    # Initialize OpenAI client
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    if not client.api_key:
      raise Exception("OpenAI API key not found")

    response = client.chat.completions.create(
      model="gpt-4",
      messages=[{
          "role":
          "system",
          "content":
          """You are an engineer, create a list of requests in python code that makes the content of a
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
                  } Thank you!"""
      }, {
          "role": "user",
          "content": f"{instructions_text}"
      }],
      temperature=0,
      max_tokens=2048,
      top_p=1,
      frequency_penalty=0,
      presence_penalty=0)
  generated_code = response.choices[0].message.content
  cleaned_result = re.sub(r'^```python\n|```$',
                          '',
                          generated_code,
                          flags=re.MULTILINE)
  return cleaned_result.strip()
  except Exception as e:
    print(f"Error in generate_code_from_instructions: {str(e)}")
    raise Exception(f"Failed to generate presentation code: {str(e)}")


def run_generated_code(generated_code, credentials):
  # Build the service
  service = build('slides', 'v1', credentials=credentials)

  # Create a presentation
  presentation = service.presentations().create(body={
      'title': 'Sample Presentation'
  }).execute()
  presentation_id = presentation['presentationId']

  # Parse the JSON string
  data = json.loads(generated_code)

  for index, i in enumerate(data):
    print(i)
    try:
      requests = [i]
      response = service.presentations().batchUpdate(
          presentationId=presentation_id, body={
              'requests': requests
          }).execute()
      print(f"Successfully executed {len(requests)} requests")
    except:
      print(f"Error in request {index}")
      continue
  url = f'https://docs.google.com/presentation/d/{presentation_id}/edit'
  return presentation_id, url


def share_presentation(presentation_id, email, credentials):
  drive_service = build('drive', 'v3', credentials=credentials)
  drive_service.permissions().create(fileId=f'{presentation_id}',
                                     body={
                                         'type': 'user',
                                         'role': 'writer',
                                         'emailAddress': f'{email}'
                                     },
                                     fields='id').execute()
