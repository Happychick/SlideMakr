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
import re
import json
import time
import logging
import hashlib
from typing import Dict, List, Any, Tuple
import urllib.request

# Load environment variables
from dotenv import load_dotenv

load_dotenv()

# Lazy import globals
_psycopg2 = None
_service_account = None
_build = None
_pydub = None
_pyaudio = None
_wave = None


def get_psycopg2():
    global _psycopg2
    if _psycopg2 is None:
        import psycopg2
        import psycopg2.extras
        _psycopg2 = psycopg2
    return _psycopg2


def get_google_services():
    global _service_account, _build
    if _service_account is None:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        _service_account = service_account
        _build = build
    return _service_account, _build


def get_audio_modules():
    global _pydub, _pyaudio, _wave
    if _pydub is None:
        from pydub import AudioSegment
        import pyaudio
        import wave
        _pydub = AudioSegment
        _pyaudio = pyaudio
        _wave = wave
    return _pydub, _pyaudio, _wave


def get_db_connection():
    """Get PostgreSQL database connection"""
    try:
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            logging.error("DATABASE_URL environment variable not found")
            return None

        psycopg2 = get_psycopg2()
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
                presentation_id VARCHAR(255),
                error_code TEXT,
                correct_code TEXT,
                error_msg TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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


def db_record_error(presentation_id: str, error_code: str, error_msg: str):
    """Record error in PostgreSQL"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO slide_errors (presentation_id, error_code, error_msg)
            VALUES (%s, %s, %s)
        """, (presentation_id, error_code, error_msg))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Database record error: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def db_update_fix(presentation_id: str, error_code: str, correct_code: str):
    """Update correct code for an error"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE slide_errors 
            SET correct_code = %s 
            WHERE presentation_id = %s AND error_code = %s AND correct_code IS NULL
        """, (correct_code, presentation_id, error_code))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Database update error: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def db_get_all_errors() -> List[dict]:
    """Get all error records"""
    conn = get_db_connection()
    if not conn:
        return []

    try:
        psycopg2 = get_psycopg2()
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


def save_presentation_to_db(presentation_id, presentation_title,
                            instructions_text, email_address, started_at=None):
    """Save presentation data to the presentations table"""
    conn = get_db_connection()
    if not conn:
        logging.error("Could not connect to database to save presentation")
        return False

    try:
        cur = conn.cursor()
        if email_address is None:
            # Insert without email_address initially
            cur.execute(
                """
                INSERT INTO presentations (presentation_id, presentation_title, instructions_text, presentation_creation_started_at, presentation_created_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (presentation_id) DO NOTHING
            """, (presentation_id, presentation_title, instructions_text, started_at))
        else:
            # Update with email_address when provided
            cur.execute(
                """
                INSERT INTO presentations (presentation_id, presentation_title, instructions_text, email_address, presentation_creation_started_at, presentation_created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (presentation_id) 
                DO UPDATE SET email_address = EXCLUDED.email_address
            """, (presentation_id, presentation_title, instructions_text,
                  email_address, started_at))
        conn.commit()
        logging.info(f"Saved presentation {presentation_id} to database")
        return True
    except Exception as e:
        logging.error(f"Error saving presentation to database: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def update_presentation_email(presentation_id, email_address):
    """Update only the email address for an existing presentation"""
    conn = get_db_connection()
    if not conn:
        logging.error("Could not connect to database to update email")
        return False

    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE presentations 
            SET email_address = %s 
            WHERE presentation_id = %s
        """, (email_address, presentation_id))
        conn.commit()
        logging.info(f"Updated email for presentation {presentation_id}")
        return True
    except Exception as e:
        logging.error(f"Error updating presentation email: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def mark_presentation_completed(presentation_id):
    """Mark presentation as completed and calculate total time"""
    conn = get_db_connection()
    if not conn:
        logging.error("Could not connect to database to mark completion")
        return None

    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE presentations 
            SET presentation_completed_at = NOW()
            WHERE presentation_id = %s
            RETURNING EXTRACT(EPOCH FROM (presentation_completed_at - presentation_creation_started_at)) as total_seconds
        """, (presentation_id,))
        result = cur.fetchone()
        conn.commit()
        
        if result:
            total_seconds = result[0]
            logging.info(f"Presentation {presentation_id} completed in {total_seconds} seconds")
            return total_seconds
        return None
    except Exception as e:
        logging.error(f"Error marking presentation completed: {e}")
        return None
    finally:
        cur.close()
        conn.close()


def _set_env(var: str):
    if not os.environ.get(var):
        os.environ[var] = getpass.getpass(f"{var}: ")


# Grant access to tools
SCOPES = [
    'https://www.googleapis.com/auth/presentations',
    'https://www.googleapis.com/auth/drive'
]

# Lazy load credentials
_credentials = None


def get_credentials():
    global _credentials
    if _credentials is not None:
        return _credentials

    try:
        service_account_json = os.getenv('SERVICE_ACCOUNT_PATH')
        if service_account_json:
            service_account, _ = get_google_services()
            service_account_info = json.loads(service_account_json)
            _credentials = service_account.Credentials.from_service_account_info(
                service_account_info, scopes=SCOPES)
        else:
            logging.error(
                "SERVICE_ACCOUNT_PATH environment variable not found")
            _credentials = None
    except json.JSONDecodeError as e:
        logging.error(f"Invalid JSON in SERVICE_ACCOUNT_PATH: {e}")
        _credentials = None
    except Exception as e:
        logging.error(f"Error loading credentials: {e}")
        _credentials = None

    return _credentials


# Make credentials available as module attribute for backward compatibility
credentials = get_credentials()


def record_until_silence(threshold=30, silence_duration=4):
    """Record audio until silence is detected."""
    AudioSegment, pyaudio, wave = get_audio_modules()
    import numpy as np

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
    # Lazy import OpenAI
    from openai import OpenAI

    # Initialize OpenAI client
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    # Transcribe audio using OpenAI API
    with open(wav_buffer, "rb") as wav_file:
        try:
            response = client.audio.transcriptions.create(
                model="whisper-1", file=wav_file, response_format="text")
            print(response)
            return response
        except Exception as e:
            print(f"An error occurred: {e}")
            raise Exception(f"Transcription failed: {str(e)}")


# Generating code for Presentation

# Lazy load Anthropic client
_code_client = None


def get_code_client():
    global _code_client
    if _code_client is None:
        import anthropic
        _code_client = anthropic.Anthropic(api_key=os.getenv('CLAUDE_API_KEY'))
    return _code_client


# Make code_client available as module attribute for backward compatibility
code_client = get_code_client()

# Slide template ID with default formatting
template_id = os.getenv('SLIDE_TEMPLATE_ID')


# 1. Create Presentation
def create_presentation(code_client, credentials, instructions_text,
                        template_id, started_at=None):
    # Build the service and the presentation
    _, build = get_google_services()
    service = build('slides', 'v1', credentials=credentials)
    drive_service = build('drive', 'v3', credentials=credentials)

    # System prompt for presentation creation decisions
    creation_prompt = """You are a creative writer. Unless specified in the instructions text, e.g. make a presentation called "Boats", come up with a title for the presentation based on the themes of the instructions text. 
The other thing you must decide is whether or not to use the template or not. Instructions for that are specified below.
Return the title and whether to use the template or not in plain JSON format like this:

    {
      "title": "extracted_title_here",
      "use_template": true_or_false
    }

    Title
    - Create a clear, concise presentation title from the instructions, unless a specific title is given in the instructions.

    TEMPLATE DECISION:
    - Set "use_template" to true if NO specific design instructions are given (no colors,fonts styling mentioned)
    - Set "use_template" to false if the user specifies colors, fonts, or custom styling
    Return ONLY the JSON, nothing else."""

    # Call LLM and get the above information
    response = code_client.messages.create(model="claude-opus-4-20250514",
                                           max_tokens=200,
                                           temperature=0.5,
                                           system=creation_prompt,
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

    try:
        cleaned_result = re.sub(r'^```.*\n?|```$',
                                '',
                                response.content[0].text,
                                flags=re.MULTILINE)
        result = json.loads(cleaned_result)
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
            body={
                'name': presentation_title
            }).execute()
        presentation_id = presentation['id']
    else:
        # Create blank presentation for custom styling
        presentation = service.presentations().create(
            body={
                'title': presentation_title
            }).execute()
        presentation_id = presentation['presentationId']

    # Save presentation data to database (email will be added later during sharing)
    save_presentation_to_db(presentation_id, presentation_title,
                            instructions_text, None, started_at)
    return service, presentation_id, presentation_title, use_template


def generate_code_from_instructions(instructions_text, code_client,use_template,objects):
    
    layout_instructions = """
         For each slide, choose the most appropriate predefinedLayout from the following options:
         -  "TITLE" - Used for slides that are titles. Usually used at the start and end of a presentation
         -  "SECTION_HEADER" - Used for transitioning between sections of the presentation. E.g. Let's say in our summary, we say we will do 1. Qualitative Analysis and 2. Quantitative analysis. Each of these would be a section header slide
         -  "TITLE_AND_BODY" - for slides with a main title and supporting text, this is the most common layout
         -  "SECTION_TITLE_AND_DESCRIPTION" - Section title and description, for detailed section introductions
         -  "BIG_NUMBER" - for highlighting statistics or key metrics
    
        Inputting the chosen layout into the createSlide request like this:
        {
          "createSlide": {
              "objectId": "slide_0",
              "slideLayoutReference": {
                  "predefinedLayout": "TITLE"
              }
          }
        }
    """

    system_prompt = f"""You are an engineer, create a list of requests in python code that makes the content of a Google slides presentation from the human instructions.

The code will be used as content for requests in another function where we call the Google API so in your response start immediately with the code like this: [{{"createSlide":'. Do not include the 'request = []', or any text, like '''json, just the list.

Please format the output as valid JSON with double quotes for all property names and string values.
Every item in the request list should be formatted as a dictionary of dictionaries, like this {{}}.

Additionally, please apply styling based on this: {layout_instructions}

IMPORTANT SLIDE CREATION RULES:
1. The presentation already has a first slide created automatically. For the FIRST slide only, do NOT use "createSlide". Instead, use "insertText" operations directly on the existing slide, applying these requests to the existing objectIDs.
2. For subsequent slides (slide 2, 3, etc.), use "createSlide" with predefinedLayout. You can specify the object id's in the placeholderIdMappings section like this:

Example for SUBSEQUENT slides (create new slides):
{{
  "createSlide": {{
      "objectId": "slide_1",
      "slideLayoutReference": {{
          "predefinedLayout": "TITLE_AND_BODY"
      }},
      "placeholderIdMappings": [ 
          {{
              "layoutPlaceholder": {{
                  "type": "TITLE"
              }},
              "objectId": "title_1"
          }},
          {{
              "layoutPlaceholder": {{
                  "type": "BODY"
              }},
              "objectId": "body_1"
          }}
      ]
  }}
}},
{{
  "insertText": {{
      "objectId": "title_1",
      "insertionIndex": 0,
      "text": "Your slide title here"
  }}
}}

3. To correctly insert text into existing placeholders, you need to reference the correct object id. Unless you have defined it in the code (as part of a new request), you can find the list of existing objects in the presentation here: {objects}. You only need to create new objectIDs if the existing placeholders are not sufficient for the content.

Example for FIRST slide (use existing slide):
{{
  "insertText": {{
      "objectId": "i0",
      "insertionIndex": 0,
      "text": "Your title here"
  }}
}},
{{
  "insertText": {{
      "objectId": "i1",
      "insertionIndex": 0,
      "text": "Your subtitle here"
  }}
}}

4. Only create custom shapes if you need elements not available in the template placeholders (like tables, images, etc.). IMPORTANT: Ensure each object has a unique objectId."""

    # Generate completion
    response = code_client.messages.create(model="claude-opus-4-20250514",
                                           max_tokens=20000,
                                           temperature=0.6,
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


def run_generated_code(code_client, instructions_text, presentation_id, service,
                       use_template):
    
    presentation = service.presentations().get(presentationId=presentation_id).execute()
    objects = json.dumps(presentation.get('slides', []))  # Get the objects from the presentation
    generated_code =  generate_code_from_instructions(instructions_text, code_client, use_template,objects)
    
    try:
        requests = json.loads(generated_code)
    except json.JSONDecodeError as e:
        return "", {"json_error": str(e)}

    errors = {}

    # Execute requests one by one, fixing immediately on failure
    for i, req in enumerate(requests):
        try:
            service.presentations().batchUpdate(presentationId=presentation_id,
                                                body={
                                                    'requests': [req]
                                                }).execute()
        except Exception as e:
            error_code = json.dumps(req)
            error_message = str(e)

            # Record error in database
            db_record_error(presentation_id, error_code, error_message)

            # Try to fix immediately
            try:
                # REFRESH objects data to include any slides created since the start
                fresh_presentation = service.presentations().get(presentationId=presentation_id).execute()
                fresh_objects = json.dumps(fresh_presentation.get('slides', []))
                
                fix_prompt = f"The following {error_code} failed with this {error_message} please fix just this snippet of code without overwriting anything else. It could be that this snippet failed due to a parent failure, e.g. an InsertText object nested under a CreateShape, so please, refer to the {fresh_objects} document to get the correct objectIDs"

                fixed_code = generate_code_from_instructions(fix_prompt, code_client, use_template, fresh_objects)
                fixed_json = json.loads(fixed_code)
                fixed_req = fixed_json[0] if isinstance(fixed_json,
                                                        list) else fixed_json

                service.presentations().batchUpdate(
                    presentationId=presentation_id,
                    body={
                        'requests': [fixed_req]
                    }).execute()

                # Record the fix in database
                db_update_fix(presentation_id, error_code,
                              json.dumps(fixed_req))

            except Exception as fix_error:
                errors[
                    error_code] = f"Original error: {error_message}. Fix failed: {str(fix_error)}"

    # Mark presentation as completed and calculate time
    total_seconds = mark_presentation_completed(presentation_id)
    if total_seconds:
        logging.info(f"Total presentation creation time: {total_seconds} seconds")
    
    url = f'https://docs.google.com/presentation/d/{presentation_id}/edit'
    return url, errors


# Database initialization will be done lazily when needed


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
    _, build = get_google_services()
    drive_service = build('drive', 'v3', credentials=credentials)
    drive_service.permissions().create(fileId=f'{presentation_id}',
                                       body={
                                           'type': 'user',
                                           'role': 'writer',
                                           'emailAddress': f'{email}'
                                       },
                                       fields='id').execute()

    # Update the database with the email address
    update_presentation_email(presentation_id, email)
