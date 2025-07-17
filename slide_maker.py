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

# PostgreSQL Database utilities
import psycopg2
import psycopg2.extras

def get_db_connection():
    """Get PostgreSQL database connection"""
    try:
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            logging.error("DATABASE_URL environment variable not found")
            logging.error(f"Available env vars starting with 'DATABASE': {[k for k in os.environ.keys() if k.startswith('DATABASE')]}")
            logging.error(f"Available env vars starting with 'REPLIT': {[k for k in os.environ.keys() if k.startswith('REPLIT')]}")
            logging.error(f"Available env vars starting with 'POSTGRES': {[k for k in os.environ.keys() if k.startswith('POSTGRES')]}")
            return None
        logging.info(f"Found DATABASE_URL: {database_url[:50]}...")
        return psycopg2.connect(database_url)
    except Exception as e:
        logging.error(f"Database connection error: {e}")
        return None

def init_error_table():
    """Initialize the error tracking table"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS slide_errors (
                id SERIAL PRIMARY KEY,
                error_hash VARCHAR(32) UNIQUE,
                error_code TEXT,
                correct_code TEXT,
                error_msg TEXT,
                count INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Table creation error: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def db_set_error(error_hash: str, error_data: dict):
    """Set error data in PostgreSQL"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO slide_errors (error_hash, error_code, correct_code, error_msg, count)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (error_hash) 
            DO UPDATE SET 
                count = slide_errors.count + 1,
                updated_at = CURRENT_TIMESTAMP
        """, (error_hash, error_data['error_code'], error_data.get('correct_code'), 
              error_data['error_msg'], error_data['count']))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Database set error: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def db_get_error(error_hash: str) -> dict:
    """Get error data from PostgreSQL"""
    conn = get_db_connection()
    if not conn:
        return {}

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM slide_errors WHERE error_hash = %s", (error_hash,))
        result = cur.fetchone()
        return dict(result) if result else {}
    except Exception as e:
        logging.error(f"Database get error: {e}")
        return {}
    finally:
        cur.close()
        conn.close()

def db_update_fix(error_hash: str, correct_code: str):
    """Update correct code for an error"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE slide_errors 
            SET correct_code = %s, updated_at = CURRENT_TIMESTAMP 
            WHERE error_hash = %s
        """, (correct_code, error_hash))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Database update error: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def db_get_common_fixes(threshold: int = 10) -> List[dict]:
    """Get common fixes from PostgreSQL"""
    conn = get_db_connection()
    if not conn:
        return []

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT error_code, correct_code, error_msg, count 
            FROM slide_errors 
            WHERE count >= %s AND correct_code IS NOT NULL 
            ORDER BY count DESC
        """, (threshold,))
        return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logging.error(f"Database query error: {e}")
        return []
    finally:
        cur.close()
        conn.close()

def db_get_all_errors() -> List[dict]:
    """Get all error records"""
    conn = get_db_connection()
    if not conn:
        return []

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM slide_errors ORDER BY count DESC")
        return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logging.error(f"Database query error: {e}")
        return []
    finally:
        cur.close()
        conn.close()

def db_clear_errors():
    """Clear all error records"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM slide_errors")
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Database clear error: {e}")
        return False
    finally:
        cur.close()
        conn.close()


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


# Error tracking with PostgreSQL
class ErrorDB:
    def __init__(self):
        init_error_table()

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

            error_data = {
                'error_code': error_code,
                'correct_code': None,
                'count': 1,
                'error_msg': error_msg
            }

            db_set_error(hash_key, error_data)
        except json.JSONDecodeError:
            pass

    def update_fix(self, error_code: str, correct_code: str):
        """Update correct code for an error"""
        try:
            error_dict = json.loads(error_code)
            hash_key = self._get_structure_hash(error_dict)
            db_update_fix(hash_key, correct_code)
        except json.JSONDecodeError:
            pass

    def get_common_fixes(self, threshold: int = 10) -> List[Dict]:
        """Get fixes for common errors"""
        return db_get_common_fixes(threshold)

    def validate_code(self, code_dict: Dict) -> Tuple[bool, str]:
        """Validate code against known error patterns"""
        hash_key = self._get_structure_hash(code_dict)
        error_data = db_get_error(hash_key)

        if error_data and error_data.get('correct_code'):
            return False, error_data['correct_code']

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
    all_errors = db_get_all_errors()
    total = len(all_errors)
    with_fixes = sum(1 for error in all_errors if error.get('correct_code'))
    common = sum(1 for error in all_errors if error.get('count', 0) >= 10)

    return {'total': total, 'with_fixes': with_fixes, 'common': common}

def reset_error_db():
    """Reset error database"""
    return db_clear_errors()

def share_presentation(presentation_id, email, credentials):
  drive_service = build('drive', 'v3', credentials=credentials)
  drive_service.permissions().create(fileId=f'{presentation_id}',
                                     body={
                                         'type': 'user',
                                         'role': 'writer',
                                         'emailAddress': f'{email}'
                                     },
                                     fields='id').execute()