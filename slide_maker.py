# -*- coding: utf-8 -*-
"""Slide Maker.ipynb"""

from base64 import b64decode
from io import BytesIO
from pydub import AudioSegment

import numpy as np
from openai import OpenAI
import anthropic

import os, getpass
import tempfile
from dotenv import load_dotenv

from google.oauth2 import service_account
from googleapiclient.discovery import build
import re
import json
import uuid

import pyaudio
import wave
import time
import logging
import uuid

# Import our simple database functions
from database import (
    get_intent_by_type,
    record_presentation_object,
    initialize_system,
    get_db_connection
)

# Load environment variables
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


def db_get_all_errors() -> list[dict]:
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
                            instructions_text, email_address):
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
                INSERT INTO presentations (presentation_id, presentation_title, instructions_text)
                VALUES (%s, %s, %s)
                ON CONFLICT (presentation_id) DO NOTHING
            """, (presentation_id, presentation_title, instructions_text))
        else:
            # Update with email_address when provided
            cur.execute(
                """
                INSERT INTO presentations (presentation_id, presentation_title, instructions_text, email_address)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (presentation_id)
                DO UPDATE SET email_address = EXCLUDED.email_address
            """, (presentation_id, presentation_title, instructions_text,
                  email_address))
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
            logging.error("SERVICE_ACCOUNT_PATH environment variable not found")
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
                        template_id):
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
                            instructions_text, None)
    return service, presentation_id, presentation_title, use_template


def identify_intents_from_instructions(instructions_text: str, use_template: bool = True):
    """Map user instructions to Google Slides API intents"""

    # Get available intents directly from database
    conn = get_db_connection()
    intent_descriptions = "No intents available"
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT intent_type, description FROM intent_to_api ORDER BY intent_type")
            results = cur.fetchall()
            intent_descriptions = "\n".join([f"- {row[0]}: {row[1]}" for row in results])
        except Exception as e:
            logging.error(f"Error getting intents: {e}")
        finally:
            cur.close()
            conn.close()

    system_prompt = f"""You are an expert at mapping user presentation instructions to specific Google Slides API intents.

Available API intents:
{intent_descriptions}

TEMPLATE LAYOUTS (use when use_template=True):
- p2: Professional content slides
- p3: Section headers
- p4: Title and body layout
- p9: Section with description
- p11: Big number/statistics
- BLANK: Custom styling (use_template=False)

MAPPING EXAMPLES:
User says: "Create a presentation with 2 slides, second has a table"
Intents needed: createSlide, createSlide, createTable

User says: "Add bullet points to slide 1"
Intents needed: createParagraphBullets

User says: "Insert image at position 100,200"
Intents needed: createImage

Return JSON with intents array containing intent_type and parameters:
{{
  "intents": [
    {{
      "intent_type": "createSlide",
      "parameters": {{
        "layout_id": "p4"
      }}
    }},
    {{
      "intent_type": "insertText",
      "parameters": {{
        "text": "Sample text"
      }}
    }}
  ]
}}

IMPORTANT: For first slide, don't use createSlide - use insertText directly on existing slide.
"""

    code_client = get_code_client()
    response = code_client.messages.create(
        model="claude-opus-4-20250514",
        max_tokens=3000,
        temperature=0.3,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": [{
                "type": "text",
                "text": f"Instructions: {instructions_text}\nUse template: {use_template}"
            }]
        }]
    )

    try:
        cleaned_result = re.sub(r'^```.*\n?|```$', '', response.content[0].text, flags=re.MULTILINE)
        result = json.loads(cleaned_result)
        return result.get('intents', [])
    except (json.JSONDecodeError, KeyError) as e:
        logging.error(f"Error parsing intents: {e}")
        return []

def convert_intents_to_api_requests(intents: list, presentation_id: str):
    """Convert intents to Google API requests"""
    all_requests = []
    object_context = {}  # Track objects for dependencies

    for intent_data in intents:
        intent_template = get_intent_by_type(intent_data['intent_type'])
        if not intent_template:
            logging.warning(f"Unknown intent: {intent_data['intent_type']}")
            continue

        # Generate object IDs
        object_ids = {}
        parameters = json.loads(intent_template['parameters'])

        for param in parameters:
            if param == 'slide_id':
                object_ids[param] = object_context.get('current_slide_id', f"slide_{uuid.uuid4().hex[:8]}")
            elif 'object_id' in param:
                object_ids[param] = f"obj_{uuid.uuid4().hex[:8]}"
            elif param == 'layout_id':
                # Use your existing template system
                object_ids[param] = intent_data['parameters'].get('layout_id', 'p2')
            else:
                # Use provided values or defaults
                provided = intent_data['parameters'].get(param)
                if provided is not None:
                    object_ids[param] = str(provided)
                else:
                    # Simple defaults
                    defaults = {
                        'insertion_index': '0',
                        'x_position': '50', 'y_position': '50',
                        'height': '200', 'width': '400',
                        'rows': '3', 'columns': '3',
                        'shape_type': 'TEXT_BOX',
                        'match_case': 'false',
                        'linking_mode': 'LINKED'
                    }
                    object_ids[param] = defaults.get(param, f"default_{param}")

        # Fill template
        api_template = json.loads(intent_template['api_template'])
        template_str = json.dumps(api_template)

        for key, value in object_ids.items():
            template_str = template_str.replace(f"{{{key}}}", str(value))

        try:
            filled_requests = json.loads(template_str)
            all_requests.extend(filled_requests)

            # Track objects
            for key, value in object_ids.items():
                if key == 'slide_id':
                    object_context['current_slide_id'] = value
                if 'object_id' in key:
                    record_presentation_object(presentation_id, value, intent_data['intent_type'])

        except json.JSONDecodeError as e:
            logging.error(f"Template error for {intent_data['intent_type']}: {e}")

    return all_requests


def generate_code_from_instructions(instructions_text, code_client,
                                    use_template, presentation_id=None):
    """New RAG approach - maps to intents, then converts to API requests"""
    # Step 1: Map instructions to intents
    intents = identify_intents_from_instructions(instructions_text, use_template)

    # Step 2: Convert intents to API requests
    api_requests = convert_intents_to_api_requests(intents, presentation_id or "temp_presentation_id")

    # Step 3: Return as JSON string (to match existing interface)
    return json.dumps(api_requests)


def run_generated_code(code_client, generated_code, presentation_id, service,
                       use_template):
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
            fix_prompt = f"The following {error_code} failed with this {error_message} please fix just this snippet of code without overwriting anything else. It could be that this snippet failed due to a parent failure, e.g. an InsertText object nested under a CreateShape, so please, read the contents of the error to decide best next steps."

            try:
                fixed_code = generate_code_from_instructions(
                    fix_prompt, code_client, use_template)
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

    url = f'https://docs.google.com/presentation/d/{presentation_id}/edit'
    return url, errors


# Database initialization will be done lazily when needed


def get_error_stats():
    """Get database statistics"""
    conn = get_db_connection()
    if not conn:
        return {'total': 0, 'with_fixes': 0, 'common': 0}

    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM slide_errors")
        total = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM slide_errors WHERE correct_code IS NOT NULL")
        with_fixes = cur.fetchone()[0]

        return {'total': total, 'with_fixes': with_fixes, 'common': 0}
    except Exception as e:
        logging.error(f"Error getting stats: {e}")
        return {'total': 0, 'with_fixes': 0, 'common': 0}
    finally:
        cur.close()
        conn.close()


def reset_error_db():
    """Reset error database"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM slide_errors")
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error clearing errors: {e}")
        return False
    finally:
        cur.close()
        conn.close()


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
```.*\n?|```$',
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
                            instructions_text, None)
    return service, presentation_id, presentation_title, use_template


def identify_intents_from_instructions(instructions_text: str, use_template: bool = True):
    """Map user instructions to Google Slides API intents"""

    # Get available intents directly from database
    conn = get_db_connection()
    intent_descriptions = "No intents available"
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT intent_type, description FROM intent_to_api ORDER BY intent_type")
            results = cur.fetchall()
            intent_descriptions = "\n".join([f"- {row[0]}: {row[1]}" for row in results])
        except Exception as e:
            logging.error(f"Error getting intents: {e}")
        finally:
            cur.close()
            conn.close()

    system_prompt = f"""You are an expert at mapping user presentation instructions to specific Google Slides API intents.

Available API intents:
{intent_descriptions}

TEMPLATE LAYOUTS (use when use_template=True):
- p2: Professional content slides
- p3: Section headers
- p4: Title and body layout
- p9: Section with description
- p11: Big number/statistics
- BLANK: Custom styling (use_template=False)

MAPPING EXAMPLES:
User says: "Create a presentation with 2 slides, second has a table"
Intents needed: createSlide, createSlide, createTable

User says: "Add bullet points to slide 1"
Intents needed: createParagraphBullets

User says: "Insert image at position 100,200"
Intents needed: createImage

Return JSON with intents array containing intent_type and parameters:
{{
  "intents": [
    {{
      "intent_type": "createSlide",
      "parameters": {{
        "layout_id": "p4"
      }}
    }},
    {{
      "intent_type": "insertText",
      "parameters": {{
        "text": "Sample text"
      }}
    }}
  ]
}}

IMPORTANT: For first slide, don't use createSlide - use insertText directly on existing slide.
"""

    code_client = get_code_client()
    response = code_client.messages.create(
        model="claude-opus-4-20250514",
        max_tokens=3000,
        temperature=0.3,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": [{
                "type": "text",
                "text": f"Instructions: {instructions_text}\nUse template: {use_template}"
            }]
        }]
    )

    try:
        cleaned_result = re.sub(r'^```.*\n?|