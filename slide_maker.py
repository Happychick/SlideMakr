# slide_maker.py
"""Simplified slide maker - preserving your working template system"""

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

import pyaudio
import wave
import time
import logging
import uuid

# Import our simple database functions
from database import (
    get_intent_by_type, 
    record_presentation_object, 
    get_presentation_objects,
    save_presentation_to_db,
    record_error,
    initialize_system,
    get_db_connection
)

# Load environment variables
load_dotenv()
logging.basicConfig(level=logging.INFO)

# Set environment variables
def _set_env(var: str):
    if not os.environ.get(var):
        os.environ[var] = getpass.getpass(f"{var}: ")

_set_env("OPENAI_API_KEY")

# Google API setup
SCOPES = [
    'https://www.googleapis.com/auth/presentations',
    'https://www.googleapis.com/auth/drive'
]

try:
    service_account_json = os.getenv('SERVICE_ACCOUNT_PATH')
    if service_account_json:
        service_account_info = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info, scopes=SCOPES)
    else:
        logging.error("SERVICE_ACCOUNT_PATH environment variable not found")
        credentials = None
except Exception as e:
    logging.error(f"Error loading credentials: {e}")
    credentials = None

# Initialize clients
code_client = anthropic.Anthropic(api_key=os.getenv('CLAUDE_API_KEY'))
template_id = os.getenv('SLIDE_TEMPLATE_ID')

def identify_intents_from_instructions(instructions_text: str, use_template: bool):
    """Use Claude to map user instructions to Google Slides API intents"""

    # Get available intents from database
    conn = get_db_connection()
    if not conn:
        logging.error("No database connection for intent identification")
        return []

    try:
        cur = conn.cursor()
        cur.execute("SELECT intent_type, description FROM intent_to_api")
        available_intents = cur.fetchall()

        intent_descriptions = "\n".join([f"- {intent[0]}: {intent[1]}" for intent in available_intents])

        layout_info = """
TEMPLATE LAYOUTS (use when use_template=True):
- p2: Professional content slides  
- p3: Section headers
- p4: Title and body layout
- p9: Section with description
- p11: Big number/statistics
- BLANK: Custom styling (use_template=False)
"""

        system_prompt = f"""You are an expert at mapping user presentation instructions to specific Google Slides API intents.

Available API intents:
{intent_descriptions}

{layout_info}

CRITICAL MAPPING RULES:
1. For each slide that needs text, you MUST create a TEXT_BOX shape AND insert text into it
2. Always specify the slide_id when creating shapes or inserting text
3. Use realistic positioning: x_position and y_position in points (typical slide is 720x540 points)

MAPPING EXAMPLES:
User says: "Create a presentation with 2 slides, second slide says 'this is so cool'"
Intents needed: 
1. createSlide (slide 1)
2. createSlide (slide 2) 
3. createShape (TEXT_BOX on slide 2)
4. insertText (add "this is so cool" to the text box)

User says: "make a presentation with 2 slides, and it says this is so cool, making slides with voice"
Intents needed:
1. createSlide (slide 1 - title slide)
2. createSlide (slide 2 - content slide)
3. createShape (TEXT_BOX on slide 1 for title)
4. insertText ("Voice Slides Presentation" on slide 1)
5. createShape (TEXT_BOX on slide 2 for content)
6. insertText ("this is so cool, making slides with voice" on slide 2)

MAP THE INSTRUCTIONS: Map the user instructions to the exact API intents needed. Include all required parameters with actual values.

Return ONLY a JSON array like:
[
    {{
        "intent_type": "createSlide",
        "parameters": {{
            "layout_id": "p4"
        }},
        "order": 1
    }},
    {{
        "intent_type": "createShape", 
        "parameters": {{
            "shape_type": "TEXT_BOX",
            "height": "100",
            "width": "600", 
            "x_position": "60",
            "y_position": "100"
        }},
        "order": 2
    }},
    {{
        "intent_type": "insertText",
        "parameters": {{
            "text": "Your content here",
            "insertion_index": "0"
        }},
        "order": 3
    }}
]

Current presentation template setting: use_template={use_template}"""

        try:
            response = code_client.messages.create(
                model="claude-opus-4-20250514",
                max_tokens=6000,
                temperature=0.2,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": [{"type": "text", "text": instructions_text}]}
                ]
            )

            content = response.content[0].text.strip()
            logging.info(f"Claude response: {content}")

            # Clean up the response - remove any markdown formatting
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()

            # Parse the JSON response
            try:
                intents = json.loads(content)
            except json.JSONDecodeError as e:
                logging.error(f"JSON decode error: {e}")
                logging.error(f"Content that failed to parse: {repr(content)}")
                return []

            # Ensure it's a list
            if not isinstance(intents, list):
                logging.error(f"Expected list of intents, got: {type(intents)}")
                logging.error(f"Actual content: {intents}")
                return []

            logging.info(f"Successfully parsed {len(intents)} intents")
            return intents

        except Exception as e:
            logging.error(f"Error calling Claude API: {e}")
            return []

    finally:
        cur.close()
        conn.close()

def convert_intents_to_api_requests(intents: list, presentation_id: str):
    """Convert intents to Google API requests"""
    all_requests = []
    object_context = {}  # Track objects for dependencies

    for intent_data in intents:
        intent_template = get_intent_by_type(intent_data['intent_type'])
        if not intent_template:
            logging.warning(f"Unknown intent: {intent_data['intent_type']}")
            continue

        logging.info(f"Processing intent: {intent_data['intent_type']}")
        logging.info(f"Intent template type: {type(intent_template)}")

        # Generate object IDs
        object_ids = {}
        
        # Handle parameters - database returns as list, not string
        parameters = intent_template['parameters']
        if isinstance(parameters, str):
            try:
                parameters = json.loads(parameters)
            except (json.JSONDecodeError, TypeError):
                logging.error(f"Failed to parse parameters: {parameters}")
                continue
        
        # Parameters should be a list from the database
        if not isinstance(parameters, list):
            logging.error(f"Expected list of parameters, got: {type(parameters)}")
            continue

        for param in parameters:
            if param == 'slide_id':
                # Use slide_id from intent parameters if provided, otherwise generate one
                if 'slide_id' in intent_data['parameters']:
                    object_ids[param] = intent_data['parameters']['slide_id']
                else:
                    object_ids[param] = object_context.get('current_slide_id', f"slide_{uuid.uuid4().hex[:8]}")
            elif 'object_id' in param:
                object_ids[param] = f"obj_{uuid.uuid4().hex[:8]}"
            elif param == 'layout_id':
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

        # Handle API template - database returns as list, not string
        api_template = intent_template['api_template']
        if isinstance(api_template, str):
            try:
                api_template = json.loads(api_template)
            except (json.JSONDecodeError, TypeError):
                logging.error(f"Failed to parse api_template: {api_template}")
                continue
            
        # API template should be a list from the database
        if not isinstance(api_template, list):
            logging.error(f"Expected list for api_template, got: {type(api_template)}")
            continue
            
        template_str = json.dumps(api_template)

        # Replace placeholders
        for key, value in object_ids.items():
            template_str = template_str.replace(f"{{{key}}}", str(value))

        try:
            filled_requests = json.loads(template_str)
            if isinstance(filled_requests, list):
                all_requests.extend(filled_requests)
            else:
                all_requests.append(filled_requests)

            # Track objects for future intents
            for key, value in object_ids.items():
                if key == 'slide_id':
                    object_context['current_slide_id'] = value
                if 'object_id' in key:
                    record_presentation_object(presentation_id, value, intent_data['intent_type'])

        except json.JSONDecodeError as e:
            logging.error(f"Template filling error for {intent_data['intent_type']}: {e}")
            logging.error(f"Template string: {template_str}")

    logging.info(f"Generated {len(all_requests)} API requests")
    return all_requests

# PRESERVE YOUR EXISTING WORKING FUNCTIONS EXACTLY AS THEY WERE

def create_presentation(code_client, credentials, instructions_text, template_id):
    """Create presentation - keeping your working template logic"""
    service = build('slides', 'v1', credentials=credentials)
    drive_service = build('drive', 'v3', credentials=credentials)

    # Your existing title generation logic
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

    response = code_client.messages.create(model="claude-opus-4-20250514",
                                           max_tokens=200,
                                           temperature=0.5,
                                           system=creation_prompt,
                                           messages=[{
                                               "role": "user",
                                               "content": [{
                                                   "type": "text",
                                                   "text": f"{instructions_text}"
                                               }]
                                           }])

    try:
        cleaned_result = re.sub(r'^```.*\n?|```$', '', response.content[0].text, flags=re.MULTILINE)
        result = json.loads(cleaned_result)
        presentation_title = result["title"]
        use_template = result["use_template"]
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        presentation_title = "SlideMakr's Presentation"
        use_template = True

    # Your existing presentation creation logic
    if use_template:
        presentation = drive_service.files().copy(
            fileId=template_id,
            body={'name': presentation_title}
        ).execute()
        presentation_id = presentation['id']
        logging.info(f"Created presentation from template: ID={presentation_id}")
    else:
        presentation = service.presentations().create(
            body={'title': presentation_title}
        ).execute()
        presentation_id = presentation['presentationId']
        logging.info(f"Created blank presentation: ID={presentation_id}")

    # Verify presentation ID was extracted correctly
    if not presentation_id:
        raise Exception("Failed to extract presentation ID during creation")

    save_presentation_to_db(presentation_id, presentation_title, instructions_text, None)
    return service, presentation_id, presentation_title, use_template

def execute_api_requests(service, requests, presentation_id):
    """Execute API requests with error handling"""
    errors = {}
    successful_requests = []

    for i, request in enumerate(requests):
        try:
            service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={'requests': [request]}
            ).execute()
            successful_requests.append(request)

        except Exception as e:
            error_msg = str(e)
            request_str = json.dumps(request)
            errors[f"request_{i}"] = f"Error: {error_msg}"
            logging.error(f"API request failed: {error_msg}")
            record_error(presentation_id, request_str, error_msg)

    url = f'https://docs.google.com/presentation/d/{presentation_id}/edit'
    return url, errors

# Your existing audio functions (unchanged)
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

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav_file:
        wf = wave.open(tmp_wav_file.name, 'wb')
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(p.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
        wf.close()
        return tmp_wav_file.name

def convert_audio_segment_to_wav(audio_segment, sample_rate=16000):
    """Convert AudioSegment to WAV file"""
    if audio_segment.frame_rate != sample_rate:
        audio_segment = audio_segment.set_frame_rate(sample_rate)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav_file:
        audio_segment.export(tmp_wav_file.name, format="wav")
        return tmp_wav_file.name

def transcribe_audio(wav_buffer):
    """Transcribe audio using OpenAI Whisper"""
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    with open(wav_buffer, "rb") as wav_file:
        try:
            response = client.audio.transcriptions.create(
                model="whisper-1", file=wav_file, response_format="text")
            return response
        except Exception as e:
            raise Exception(f"Transcription failed: {str(e)}")

def share_presentation(presentation_id, email, credentials):
    """Share presentation via email"""
    drive_service = build('drive', 'v3', credentials=credentials)
    drive_service.permissions().create(
        fileId=f'{presentation_id}',
        body={
            'type': 'user',
            'role': 'writer', 
            'emailAddress': f'{email}'
        },
        fields='id'
    ).execute()

# NEW FUNCTIONS for database compatibility
def get_error_stats():
    """Get error statistics from database"""
    conn = get_db_connection()
    if not conn:
        return {'total': 0, 'with_fixes': 0, 'common': 0}

    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM slide_errors")
        total = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM slide_errors WHERE correct_code IS NOT NULL")
        with_fixes = cur.fetchone()[0]

        # For now, set common to 0 since we don't have a count column yet
        common = 0

        return {'total': total, 'with_fixes': with_fixes, 'common': common}
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
        logging.error(f"Error resetting errors: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def update_presentation_email(presentation_id, email_address):
    """Update presentation email"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE presentations SET email_address = %s WHERE presentation_id = %s",
            (email_address, presentation_id)
        )
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error updating email: {e}")
        return False
    finally:
        cur.close()
        conn.close()

# Main workflow functions
def create_presentation_from_instructions(instructions_text):
    """Main function to create presentation from instructions"""

    # Step 1: Create presentation (your working logic)
    service, presentation_id, title, use_template = create_presentation(
        code_client, credentials, instructions_text, template_id)

    # Step 2: Map instructions to API intents
    intents = identify_intents_from_instructions(instructions_text, use_template)

    if not intents:
        return f"https://docs.google.com/presentation/d/{presentation_id}/edit", {"error": "Could not parse instructions"}

    # Step 3: Convert intents to API requests  
    requests = convert_intents_to_api_requests(intents, presentation_id)

    if not requests:
        return f"https://docs.google.com/presentation/d/{presentation_id}/edit", {"error": "No API requests generated"}

    # Step 4: Execute API requests
    url, errors = execute_api_requests(service, requests, presentation_id)

    return url, errors

def create_presentation_from_audio(wav_buffer):
    """Create presentation from audio buffer"""
    try:
        # Transcribe audio
        instructions = transcribe_audio(wav_buffer)
        logging.info(f"Transcribed audio: {instructions}")

        # Create presentation using instructions
        url, errors = create_presentation_from_instructions(instructions)
        logging.info(f"Presentation created: URL={url}, Errors={errors}")

        return url, errors
    except Exception as e:
        logging.error(f"Audio processing failed: {str(e)}")
        return None, {"error": f"Audio processing failed: {str(e)}"}

# Legacy function compatibility - for server.py
def generate_code_from_instructions(instructions_text, code_client, use_template):
    """Legacy compatibility function"""
    # Map to new workflow
    intents = identify_intents_from_instructions(instructions_text, use_template)
    return json.dumps(intents)  # Return as JSON string for compatibility

def run_generated_code(code_client, generated_code, presentation_id, service, use_template):
    """Legacy compatibility function"""
    try:
        intents = json.loads(generated_code)
        requests = convert_intents_to_api_requests(intents, presentation_id)
        return execute_api_requests(service, requests, presentation_id)
    except Exception as e:
        return "", {"error": f"Failed to execute: {str(e)}"}

if __name__ == "__main__":
    # Initialize database on startup
    initialize_system()

    # Example usage
    instructions = "Create a presentation about AI trends with 3 slides: title slide, content about current AI developments, and a conclusion slide with bullet points"

    url, errors = create_presentation_from_instructions(instructions)
    print(f"Presentation created: {url}")
    if errors:
        print(f"Errors: {errors}")
    else:
        print("Success! No errors.")