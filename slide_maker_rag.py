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

# REPLACE THESE TWO FUNCTIONS IN YOUR CODE:
# 1. Replace generate_code_from_instructions with identify_intents_from_instructions
# 2. Replace run_generated_code with run_intent_based_requests
# Add this function ANYWHERE in your code (I'd put it near your other db functions):
def get_slide_objects(service, presentation_id, slide_id):
    """Get all objects on a specific slide using GET API"""
    try:
        page = service.presentations().pages().get(
            presentationId=presentation_id,
            pageObjectId=slide_id
        ).execute()

        # Extract all page elements with their IDs and types
        objects = []
        if 'pageElements' in page:
            for element in page['pageElements']:
                obj = {
                    'objectId': element.get('objectId'),
                    'type': None
                }

                # Determine object type
                if 'shape' in element:
                    obj['type'] = 'shape'
                    obj['shapeType'] = element['shape'].get('shapeType')
                    if 'text' in element['shape']:
                        obj['hasText'] = True
                elif 'table' in element:
                    obj['type'] = 'table'
                elif 'image' in element:
                    obj['type'] = 'image'
                elif 'video' in element:
                    obj['type'] = 'video'
                elif 'line' in element:
                    obj['type'] = 'line'

                objects.append(obj)

        logging.info(f"Found {len(objects)} objects on slide {slide_id}")
        return objects

    except Exception as e:
        logging.error(f"Error getting slide objects: {e}")
        return []


def get_all_intents_from_db():
    """Retrieve all intents from database for system prompt"""
    conn = get_db_connection()
    if not conn:
        return []

    try:
        psycopg2 = get_psycopg2()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT intent_type, description FROM intent_to_api")
        intents = cur.fetchall()
        return [dict(intent) for intent in intents]
    except Exception as e:
        logging.error(f"Error fetching intents: {e}")
        return []
    finally:
        cur.close()
        conn.close()


def identify_intents_from_instructions(instructions_text, code_client, use_template, slide_objects_map):
    """Identify intents from instructions using RAG approach"""

    available_intents = get_all_intents_from_db()
    if not available_intents:
        logging.error("No intents found in database")
        return []

    intent_descriptions = "\n".join([
        f"- {intent['intent_type']}: {intent['description']}"
        for intent in available_intents
    ])

    # Build slide context description
    slide_context = ""
    if slide_objects_map:
        slide_context = "\n\nEXISTING SLIDES AND OBJECTS:\n"
        for slide_id, objects in slide_objects_map.items():
            slide_context += f"\nSlide '{slide_id}' has these objects:\n"
            for obj in objects:
                slide_context += f"  - {obj['objectId']} (type: {obj['type']})\n"

    layout_instructions = """
LAYOUT MAPPING - Choose the most appropriate predefinedLayout:
- "TITLE" - Used for slides that are titles. Usually used at the start and end of a presentation
- "SECTION_HEADER" - Used for transitioning between sections of the presentation. E.g. if summary says we will do 1. Qualitative Analysis and 2. Quantitative analysis, each would be a section header slide
- "TITLE_AND_BODY" - For slides with a main title and supporting text, this is the most common layout
- "SECTION_TITLE_AND_DESCRIPTION" - Section title and description, for detailed section introductions
- "BIG_NUMBER" - For highlighting statistics or key metrics
- "TITLE_AND_TWO_COLUMNS" - For two-column layouts
- "BLANK" - For blank canvas
- "CAPTION_ONLY" - For caption only slides
- "ONE_COLUMN_TEXT" - For single column text
- "MAIN_POINT" - For main point emphasis
"""

    system_prompt = f"""You are an engineer creating a Google Slides presentation from human instructions.

Available intents (API operations):
{intent_descriptions}

CONTEXT:
- ALWAYS use predefinedLayout enums from the layout mapping below
- These enums work for ALL presentations (with or without templates)

{layout_instructions}

{slide_context}

CRITICAL RULES:
1. FIRST SLIDE: The presentation already has a first slide created automatically. For the FIRST slide only:
   - DO NOT use "createSlide" 
   - USE the existing objectIds shown above (like "i0", "i1") with insertText operations
   - Only create NEW objects if the existing placeholders are insufficient

2. SUBSEQUENT SLIDES: For slides 2, 3, etc.:
   - Use "createSlide" intent with predefinedLayout enum
   - After creating slides, use insertText with the objectIds from those slides

3. OBJECT IDS:
   - If objects exist (shown above): USE their actual objectIds for insertText, updateTextStyle
   - If you need NEW elements: Use createShape/createTable/createImage first, THEN insertText
   - Ensure each object has a unique objectId

4. EMU UNITS: Use EMU units (1 inch = 9144000 EMU, slide is 9144000 x 5143500 EMU)

Return JSON array of intents in execution order.

Example WITH existing first slide objects:
[
  {{
    "intent": "insertText",
    "parameters": {{
      "OBJECT_ID": "i0",
      "TEXT": "My Title",
      "INSERTION_INDEX": "0"
    }},
    "order": 1
  }},
  {{
    "intent": "createSlide",
    "parameters": {{
      "SLIDE_ID": "slide_1",
      "INDEX": "1",
      "LAYOUT": "TITLE_AND_BODY"
    }},
    "order": 2
  }}
]

Example creating NEW slides from scratch:
[
  {{
    "intent": "createSlide",
    "parameters": {{
      "SLIDE_ID": "slide_1",
      "INDEX": "0",
      "LAYOUT": "TITLE"
    }},
    "order": 1
  }},
  {{
    "intent": "createSlide",
    "parameters": {{
      "SLIDE_ID": "slide_2",
      "INDEX": "1",
      "LAYOUT": "TITLE_AND_BODY"
    }},
    "order": 2
  }}
]

Return ONLY valid JSON."""

    try:
        response = code_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=20000,
            temperature=0.6,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": f"User instructions: {instructions_text}\n\nIdentify the required API intents and return the JSON array."
            }]
        )

        content = response.content[0].text
        logging.info(f"Raw LLM response: {content[:500]}")  # Log first 500 chars

        # Remove markdown code fences if present
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', content, flags=re.MULTILINE).strip()

        # Try to extract JSON array if it's embedded in text
        if not cleaned.startswith('['):
            # Look for the first [ and last ]
            start = cleaned.find('[')
            end = cleaned.rfind(']')
            if start != -1 and end != -1:
                cleaned = cleaned[start:end+1]

        intents = json.loads(cleaned)
        logging.info(f"Identified {len(intents)} intents")
        return intents

    except Exception as e:
        logging.error(f"Intent identification error: {e}")
        logging.error(f"Failed to parse content: {content if 'content' in locals() else 'No content'}")
        return []


def build_api_requests_from_intents(intents, presentation_id):
    """Convert identified intents into actual API requests using database templates"""

    conn = get_db_connection()
    if not conn:
        return []

    requests = []

    try:
        psycopg2 = get_psycopg2()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Sort intents by order
        sorted_intents = sorted(intents, key=lambda x: x.get('order', 0))

        for intent_data in sorted_intents:
            intent_type = intent_data['intent']
            parameters = intent_data['parameters']

            # Get template from database
            cur.execute(
                "SELECT api_template FROM intent_to_api WHERE intent_type = %s",
                (intent_type,)
            )
            result = cur.fetchone()

            if not result:
                logging.warning(f"Intent {intent_type} not found in database")
                continue

            # Get template and fill placeholders
            template = result['api_template']
            template_str = json.dumps(template)

            # Replace all placeholders with actual values
            for key, value in parameters.items():
                placeholder = f"{{{{{key}}}}}"

                # Handle different value types
                if isinstance(value, (dict, list)):
                    template_str = template_str.replace(f'"{placeholder}"', json.dumps(value))
                elif isinstance(value, bool):
                    template_str = template_str.replace(f'"{placeholder}"', str(value).lower())
                elif isinstance(value, (int, float)):
                    template_str = template_str.replace(f'"{placeholder}"', str(value))
                else:
                    template_str = template_str.replace(placeholder, str(value))

            # Parse back to dict
            filled_request = json.loads(template_str)
            requests.append(filled_request)

        return requests

    except Exception as e:
        logging.error(f"Request building error: {e}")
        return []
    finally:
        cur.close()
        conn.close()


def run_intent_based_requests(code_client, instructions_text, presentation_id, service, use_template):
    """Execute presentation creation using RAG intent system with self-healing"""

    # Step 1: Get first slide objects (automatically created)
    presentation = service.presentations().get(presentationId=presentation_id).execute()
    existing_slides = presentation.get('slides', [])

    first_slide_objects = {}
    if existing_slides:
        first_slide_id = existing_slides[0]['objectId']
        objects = get_slide_objects(service, presentation_id, first_slide_id)
        first_slide_objects[first_slide_id] = objects
        logging.info(f"First slide '{first_slide_id}' has {len(objects)} objects")

    # Step 2: Identify intents with first slide context
    logging.info("Identifying intents...")
    intents = identify_intents_from_instructions(
        instructions_text, 
        code_client, 
        use_template,
        first_slide_objects
    )

    if not intents:
        logging.error("No intents identified")
        return "", {"error": "Failed to identify intents"}

    logging.info(f"Identified {len(intents)} intents")

    # Step 3: Separate slide creation from content operations
    slide_creation_intents = [i for i in intents if i['intent'] == 'createSlide']
    content_intents = [i for i in intents if i['intent'] != 'createSlide']

    logging.info(f"- {len(slide_creation_intents)} slide creation(s)")
    logging.info(f"- {len(content_intents)} content operation(s)")

    # Step 4: Execute slide creations in batch
    if slide_creation_intents:
        logging.info("Creating slides...")
        slide_requests = build_api_requests_from_intents(slide_creation_intents, presentation_id)

        try:
            service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={'requests': slide_requests}
            ).execute()
            logging.info(f"✓ Created {len(slide_requests)} slide(s)")
        except Exception as e:
            logging.error(f"✗ Slide creation failed: {e}")

    # Step 5: GET all slide objects now
    time.sleep(0.5)  # Brief delay

    presentation = service.presentations().get(presentationId=presentation_id).execute()
    all_slides = presentation.get('slides', [])

    all_slide_objects = {}
    for slide in all_slides:
        slide_id = slide['objectId']
        objects = get_slide_objects(service, presentation_id, slide_id)
        all_slide_objects[slide_id] = objects
        logging.info(f"✓ Slide '{slide_id}' has {len(objects)} object(s)")

    # Step 6: Re-identify content with COMPLETE object map
    logging.info("Re-evaluating intents with complete slide map...")
    all_intents = identify_intents_from_instructions(
        instructions_text,
        code_client,
        use_template,
        all_slide_objects
    )

    # Filter out createSlide (already done)
    content_intents = [i for i in all_intents if i['intent'] != 'createSlide']
    logging.info(f"Final content operations: {len(content_intents)}")

    # Step 7: Execute content operations with self-healing
    errors = {}

    if content_intents:
        content_requests = build_api_requests_from_intents(content_intents, presentation_id)

        for i, req in enumerate(content_requests):
            try:
                service.presentations().batchUpdate(
                    presentationId=presentation_id,
                    body={'requests': [req]}
                ).execute()
                logging.info(f"✓ Request {i+1}/{len(content_requests)} successful")

            except Exception as e:
                error_code = json.dumps(req)
                error_message = str(e)

                # Record error
                db_record_error(presentation_id, error_code, error_message)

                # SELF-HEALING: Try to fix
                try:
                    # Refresh objects
                    fresh_presentation = service.presentations().get(
                        presentationId=presentation_id
                    ).execute()

                    fresh_slides = fresh_presentation.get('slides', [])
                    fresh_slide_objects = {}
                    for slide in fresh_slides:
                        slide_id = slide['objectId']
                        objs = get_slide_objects(service, presentation_id, slide_id)
                        fresh_slide_objects[slide_id] = objs

                    fix_prompt = f"""The following request failed: {error_code}

Error message: {error_message}

Please fix just this snippet by checking you are using the right intent without overwriting anything else. 
It could be that this snippet failed due to a parent failure (e.g. InsertText referencing a non-existent shape).

Here are the CURRENT objects in the presentation:
{json.dumps(fresh_slide_objects, indent=2)}

Return ONLY a JSON array with the corrected intent(s)."""

                    fixed_intents = identify_intents_from_instructions(
                        fix_prompt,
                        code_client,
                        use_template,
                        fresh_slide_objects
                    )

                    if fixed_intents:
                        fixed_requests = build_api_requests_from_intents(fixed_intents, presentation_id)
                        fixed_req = fixed_requests[0] if fixed_requests else None

                        if fixed_req:
                            service.presentations().batchUpdate(
                                presentationId=presentation_id,
                                body={'requests': [fixed_req]}
                            ).execute()

                            # Record the fix
                            db_update_fix(presentation_id, error_code, json.dumps(fixed_req))
                            logging.info(f"✓ Self-healed error for request {i+1}")
                except Exception as heal_error:
                    logging.error(f"✗ Self-healing failed: {heal_error}")
                    errors[error_code] = f"Original: {error_message}. Heal failed: {str(heal_error)}"

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