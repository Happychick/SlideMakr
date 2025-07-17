# Updated system prompt with better instructions for slide generation.
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


_set_env("OPENAI_API_KEY") # Can I delete this?

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

# Generating code for Presentation 

# Initialize Anthropic Client
code_client = anthropic.Anthropic(api_key=os.getenv('CLAUDE_API_KEY'))
# Slide template ID with default formatting
template_id = os.getenv('SLIDE_TEMPLATE_ID')

# 1. Create Presentation
def create_presentation(code_client,credentials,instructions_text,template_id):
  # Build the service and the presentation
  service = build('slides', 'v1', credentials=credentials)
  drive_service = build('drive', 'v3', credentials=credentials)

  # System prompt for presentation creation decisions
  creation_prompt = """You are helping create a Google Slides presentation. 
    Analyze the user's instructions and return a valid JSON response with this          format:
    {
      "title": "extracted_title_here",
      "use_template": true_or_false
    }

    TITLE EXTRACTION:
    - Extract a clear, concise presentation title from the instructions

    TEMPLATE DECISION:
    - Set "use_template" to true if NO specific design instructions are given (no colors,fonts styling mentioned)
    - Set "use_template" to false if the user specifies colors, fonts, or custom styling
    Return ONLY the JSON, nothing else."""

  # Call LLM and get the above information
  response = code_client.messages.create(model="claude-opus-4-20250514",
      max_tokens=200,
      temperature=0.3,
      system=creation_prompt,
      messages=[{
        "role": "user",
        "content": [{
            "type": "text", 
            "text": f"{instructions_text}"
        }]
      }]
  )

  try:
    result = json.loads(response.content[0].text)
    presentation_title = result["title"]
    use_template = result["use_template"]
  except (json.JSONDecodeError, KeyError, IndexError) as e:
    # Fallback if LLM fails to return proper JSON
    presentation_title = "SlideMakr's Presentation"
    use_template = True

  # Create presentation (with or without template)
  if use_template:
  # Copy template to get all styling, theme, and layouts
  # For copying, need to use Google Drive API
    presentation = drive_service.files().copy(
        fileId=template_id,  # Drive API uses 'fileId' not 'presentationId'
        body={'name': presentation_title}
    ).execute()
    presentation_id = presentation['id']
  else:
  # Create blank presentation for custom styling
    presentation = service.presentations().create(
        body={'title': presentation_title
        }).execute()
    presentation_id = presentation['presentationId']

  return service, presentation_id, presentation_title, use_template

def generate_code_from_instructions(instructions_text,code_client,use_template):

  # Build system prompt with common fixes
  common_fixes = error_db.get_common_fixes(threshold=10)
  error_examples = ""

  if common_fixes:
    error_examples = "\n\nCOMMON ERROR CORRECTIONS:\n"
    for i, fix in enumerate(common_fixes[:5]):
      error_examples += f"{i+1}. Instead of: {fix['error_code']}\n"
      error_examples += f"   Use: {fix['correct_code']}\n"
      error_examples += f"   (Occurred {fix['count']} times)\n\n"

   # Build layout instructions based on template usage
  if use_template:
     layout_instructions = """
     AVAILABLE TEMPLATE LAYOUTS (choose the most appropriate):
     - "p": Title Slide (for presentation titles)
     - "p2": Content Slide (for bullet points, text)  
     - "p3": Two Column (for comparisons)
     - "p4": Image and Text (for visual content)
     - "p5": Section Header (for new sections)

    Use template layouts like this:
    {
      "createSlide": {
          "objectId": "slide_0",
          "slideLayoutReference": {
              "layoutId": "p2"  // Choose appropriate layout ID
          }
      }
    }
    Choose the layout that best fits each slide's content automatically."""
  else:
      layout_instructions = """
    User specified custom design. Use BLANK layout and create custom styling to make sure the slides look professional:
    {
      "createSlide": {
          "objectId": "slide_0",
          "slideLayoutReference": {
              "predefinedLayout": "BLANK"
          }
      }
    }
    """

  system_prompt = f"""You are an engineer, create a list of requests in python code that makes the content of a Google slides presentation from the human instructions.

The code will be used as content for requests in another function where we call the Google API so in your response start immediately with the code like this: [{{"createSlide":'. Do not include the 'request = []', or any text, like '''json, just the list.

Please format the output as valid JSON with double quotes for all property names and string values.
Every item in the request list should be formatted as a dictionary of dictionaries, like this {{}}.

Additionally, please apply styling based on this: {layout_instructions}

CRITICAL RULES:
1. ALWAYS use unique object IDs - append timestamp or counter: "slide_1", "textbox_1_123456", etc.
2. For pageObjectId, use the slide's objectId (like "slide_0"), NEVER use layout IDs
3. Create slides first, then add content to those specific slide IDs

Example sequence:
{{
  "createSlide": {{
      "objectId": "slide_0",
      "slideLayoutReference": {{
          "predefinedLayout": "TITLE_AND_BODY"
      }}
  }}
}},
{{
  "createShape": {{
      "objectId": "textbox_0_title",
      "shapeType": "TEXT_BOX",
      "elementProperties": {{
          "pageObjectId": "slide_0",
          "size": {{"height": {{"magnitude": 100, "unit": "PT"}}, "width": {{"magnitude": 600, "unit": "PT"}}}},
          "transform": {{"translateX": 50, "translateY": 50, "unit": "PT"}}
      }}
  }}
}},
{{
  "insertText": {{
      "objectId": "textbox_0_title",
      "text": "Your slide title here"
  }}
}}"""

  # Generate completion
  response = code_client.messages.create(model="claude-opus-4-20250514",
                                         max_tokens=20000,
                                         temperature=0.6,
                                         system=system_prompt,
                                         messages=[{
                                             "role":
                                             "user",
                                             "content": [{
                                                 "type":"text",
                                                 "text":f"{instructions_text}"
                                             }]
                                         }])

  generated_code = response.content[0].text
  cleaned_result = re.sub(r'^```python\n|```$', '', generated_code, flags=re.MULTILINE).strip()

  try:
    # Parse the cleaned result as JSON
    slide_requests = json.loads(cleaned_result)
    return slide_requests

  except json.JSONDecodeError as e:
    # Handle JSON decoding errors
    print(f"JSONDecodeError: {e}")
    print(f"Failed to parse generated code: {cleaned_result}")
    raise Exception(f"Invalid JSON format in generated code: {str(e)}")

def execute_slide_requests(service, presentation_id, slide_requests):
  """Executes a list of slide requests using the Google Slides API."""
  # Batch update the presentation
  body = {'requests': slide_requests}
  try:
    response = service.presentations().batchUpdate(
        presentationId=presentation_id, body=body).execute()
    # Log the response for debugging
    print(f"Batch update response: {response}")
    return response
  except Exception as e:
    # Handle API errors
    print(f"An error occurred during batchUpdate: {e}")
    raise Exception(f"Failed to execute slide requests: {str(e)}")

def create_slides_from_instructions(instructions_text,code_client,credentials,template_id,error_db):
  """
    Generates code from instructions, creates a Google Slides presentation,
    and populates it with content based on the generated code.
    """
  # 1. Create Presentation
  service, presentation_id, presentation_title, use_template = create_presentation(code_client,credentials,instructions_text,template_id)

  # 2. Generate Code from Instructions
  slide_requests = generate_code_from_instructions(instructions_text,code_client,use_template)

  # 3. Execute Slide Requests
  execute_slide_requests(service, presentation_id, slide_requests)

  return presentation_id, presentation_title

if __name__ == '__main__':
  # For testing purposes
  instructions = "Create a presentation about the solar system. The first slide should be a title slide with the title 'The Solar System'. The second slide should be about the planets."
  presentation_id, presentation_title = create_slides_from_instructions(instructions)
  print(f"Presentation created with ID: {presentation_id} and title: {presentation_title}")