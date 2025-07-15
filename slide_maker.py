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
import hashlib
from typing import Dict, List, Any, Tuple
import urllib.request
import urllib.parse

# Load environment variables
load_dotenv()

# Replit Database utilities
def get_db_url():
    """Get Replit database URL from environment or file"""
    db_url = os.getenv("REPLIT_DB_URL")
    if not db_url:
        try:
            with open("/tmp/replitdb", "r") as f:
                db_url = f.read().strip()
        except FileNotFoundError:
            pass
    return db_url

def db_set(key: str, value: str):
    """Set a key-value pair in Replit database"""
    db_url = get_db_url()
    if not db_url:
        return False
    
    try:
        data = urllib.parse.urlencode({key: value}).encode()
        req = urllib.request.Request(db_url, data=data, method='POST')
        urllib.request.urlopen(req)
        return True
    except Exception as e:
        logging.error(f"Database set error: {e}")
        return False

def db_get(key: str) -> str:
    """Get a value from Replit database"""
    db_url = get_db_url()
    if not db_url:
        return ""
    
    try:
        url = f"{db_url}/{urllib.parse.quote(key)}"
        response = urllib.request.urlopen(url)
        return response.read().decode()
    except Exception as e:
        logging.error(f"Database get error: {e}")
        return ""

def db_delete(key: str):
    """Delete a key from Replit database"""
    db_url = get_db_url()
    if not db_url:
        return False
    
    try:
        url = f"{db_url}/{urllib.parse.quote(key)}"
        req = urllib.request.Request(url, method='DELETE')
        urllib.request.urlopen(req)
        return True
    except Exception as e:
        logging.error(f"Database delete error: {e}")
        return False

def db_list(prefix: str = "") -> List[str]:
    """List keys with optional prefix from Replit database"""
    db_url = get_db_url()
    if not db_url:
        return []
    
    try:
        url = f"{db_url}?prefix={urllib.parse.quote(prefix)}"
        response = urllib.request.urlopen(url)
        keys = response.read().decode().strip()
        return keys.split('\n') if keys else []
    except Exception as e:
        logging.error(f"Database list error: {e}")
        return []


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
        # Parse the JSON to validate it and handle any formatting issues
        service_account_info = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info, scopes=SCOPES)
    else:
        logging.error("SERVICE_ACCOUNT_PATH environment variable not found")
        credentials = None
except json.JSONDecodeError as e:
    logging.error(f"Invalid JSON in SERVICE_ACCOUNT_PATH: {e}")
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
  code_client = anthropic.Anthropic(api_key=os.getenv('CLAUDE_API_KEY'))
  
  # Build system prompt with common fixes
  common_fixes = error_db.get_common_fixes(threshold=10)
  error_examples = ""
  
  if common_fixes:
    error_examples = "\n\nCOMMON ERROR CORRECTIONS:\n"
    for i, fix in enumerate(common_fixes[:5]):
      error_examples += f"{i+1}. Instead of: {fix['error_code']}\n"
      error_examples += f"   Use: {fix['correct_code']}\n"
      error_examples += f"   (Occurred {fix['count']} times)\n\n"
  
  system_prompt = f"""You are an engineer, create a list of requests in python code that makes the content of a Google slides presentation from the human instructions.

The code will be used as content for requests in another function where we call the Google API so in your response start immediately with the code like this: [{{"createSlide":'. Do not include the 'request = []', or any text, like '''json, just the list.

Please format the output as valid JSON with double quotes for all property names and string values.
Every item in the request list should be formatted as a dictionary of dictionaries, like this {{}}.

Here is an example of a request item for createSlide: {{
  "createSlide": {{
      "objectId": f"slide_{{len(requests)}}",
      "slideLayoutReference": {{
          "predefinedLayout": slide_info.get("slideType", "BLANK")
      }}
  }}
}} Thank you!{error_examples}"""

  # Generate completion
  response = code_client.messages.create(model="claude-opus-4-20250514",
                                         max_tokens=20000,
                                         temperature=1,
                                         system=system_prompt,
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
  # First validate and fix the code using error database
  validated_code, fixes_applied = validate_generated_code(generated_code)
  
  try:
    requests = json.loads(validated_code)
  except json.JSONDecodeError as e:
    return "", {"json_error": str(e)}

  errors = {}
  fixed_requests = []
  
  # Execute validated requests
  for req in requests:
    try:
      service.presentations().batchUpdate(presentationId=presentation_id,
                                        body={'requests': [req]}).execute()
      fixed_requests.append(req)
    except Exception as e:
      error_code = json.dumps(req)
      error_message = str(e)
      errors[error_code] = error_message
      error_db.record_error(error_code, error_message)

  # Fix remaining errors
  if errors:
    for failed_req, error in list(errors.items()):
      fix_prompt = f"Fix this failed request: {failed_req} Error: {error}"
      fixed_code = generate_code_from_instructions(fix_prompt)
      
      try:
        fixed_json = json.loads(fixed_code)
        fixed_req = fixed_json[0] if isinstance(fixed_json, list) else fixed_json
        
        service.presentations().batchUpdate(presentationId=presentation_id,
                                          body={'requests': [fixed_req]}).execute()
        fixed_requests.append(fixed_req)
        error_db.update_fix(failed_req, json.dumps(fixed_req))
        errors.pop(failed_req, None)
      except Exception as e:
        errors[failed_req] = f"Fix attempt failed: {str(e)}"

  url = f'https://docs.google.com/presentation/d/{presentation_id}/edit'
  return url, errors


# Error tracking with Replit Database
class ErrorDB:
    def __init__(self):
        self.prefix = "error_"
    
    def _get_structure_hash(self, code_dict: Dict) -> str:
        """Generate hash based on code structure, ignoring specific values"""
        def normalize(obj):
            if isinstance(obj, dict):
                return {k: normalize(v) if k in ['slideLayoutReference', 'pageProperties'] 
                       else "VALUE" for k, v in obj.items()}
            elif isinstance(obj, list):
                return [normalize(item) for item in obj]
            else:
                return "VALUE"
        
        normalized = normalize(code_dict)
        return hashlib.md5(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
    
    def record_error(self, error_code: str, error_msg: str):
        """Record or increment error count"""
        try:
            error_dict = json.loads(error_code)
            hash_key = self._get_structure_hash(error_dict)
            key = f"{self.prefix}{hash_key}"
            
            existing_data = db_get(key)
            if existing_data:
                error_info = json.loads(existing_data)
                error_info['count'] += 1
            else:
                error_info = {
                    'error_code': error_code,
                    'correct_code': None,
                    'count': 1,
                    'error_msg': error_msg
                }
            
            db_set(key, json.dumps(error_info))
        except json.JSONDecodeError:
            pass
    
    def update_fix(self, error_code: str, correct_code: str):
        """Update correct code for an error"""
        try:
            error_dict = json.loads(error_code)
            hash_key = self._get_structure_hash(error_dict)
            key = f"{self.prefix}{hash_key}"
            
            existing_data = db_get(key)
            if existing_data:
                error_info = json.loads(existing_data)
                error_info['correct_code'] = correct_code
                db_set(key, json.dumps(error_info))
        except json.JSONDecodeError:
            pass
    
    def get_common_fixes(self, threshold: int = 10) -> List[Dict]:
        """Get fixes for common errors"""
        error_keys = db_list(self.prefix)
        common_fixes = []
        
        for key in error_keys:
            data = db_get(key)
            if data:
                try:
                    error_info = json.loads(data)
                    if error_info['count'] >= threshold and error_info['correct_code']:
                        common_fixes.append(error_info)
                except json.JSONDecodeError:
                    continue
        
        return common_fixes
    
    def validate_code(self, code_dict: Dict) -> Tuple[bool, str]:
        """Validate code against known error patterns"""
        hash_key = self._get_structure_hash(code_dict)
        key = f"{self.prefix}{hash_key}"
        
        data = db_get(key)
        if data:
            try:
                error_info = json.loads(data)
                if error_info['correct_code']:
                    return False, error_info['correct_code']
            except json.JSONDecodeError:
                pass
        
        return True, ""

# Global database instance
error_db = ErrorDB()

def validate_generated_code(generated_code: str) -> Tuple[str, List[str]]:
    """
    Validate generated code against database and fix known error patterns.
    Returns: (corrected_code, list_of_fixes_applied)
    """
    try:
        requests = json.loads(generated_code)
    except json.JSONDecodeError as e:
        return generated_code, [f"JSON parsing error: {str(e)}"]
    
    fixed_requests = []
    fixes_applied = []
    
    for i, req in enumerate(requests):
        is_valid, correct_code = error_db.validate_code(req)
        
        if not is_valid:
            # Replace with known correct code
            try:
                fixed_req = json.loads(correct_code)
                fixed_requests.append(fixed_req)
                fixes_applied.append(f"Fixed request {i}: Applied known correction")
            except json.JSONDecodeError:
                fixed_requests.append(req)
                fixes_applied.append(f"Request {i}: Correction failed to parse")
        else:
            fixed_requests.append(req)
    
    return json.dumps(fixed_requests), fixes_applied

def get_error_stats():
    """Get database statistics"""
    error_keys = db_list(error_db.prefix)
    total = len(error_keys)
    with_fixes = 0
    common = 0
    
    for key in error_keys:
        data = db_get(key)
        if data:
            try:
                error_info = json.loads(data)
                if error_info['correct_code']:
                    with_fixes += 1
                if error_info['count'] >= 10:
                    common += 1
            except json.JSONDecodeError:
                continue
    
    return {'total': total, 'with_fixes': with_fixes, 'common': common}

def reset_error_db():
    """Reset error database"""
    error_keys = db_list(error_db.prefix)
    for key in error_keys:
        db_delete(key)

def share_presentation(presentation_id, email, credentials):
  drive_service = build('drive', 'v3', credentials=credentials)
  drive_service.permissions().create(fileId=f'{presentation_id}',
                                     body={
                                         'type': 'user',
                                         'role': 'writer',
                                         'emailAddress': f'{email}'
                                     },
                                     fields='id').execute()
