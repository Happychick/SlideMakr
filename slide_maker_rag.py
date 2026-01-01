# -*- coding: utf-8 -*-
"""
SlideMakr - Optimized Production Version

Key optimizations:
1. Cached intents database (load once)
2. Cached layouts per presentation
3. Batched template fetching (1 query not 30)
4. Clean, single-responsibility functions
5. Clear error messages
"""

from base64 import b64decode
from io import BytesIO
from pydub import AudioSegment

import numpy as np
import whisper
import soundfile as sf
from openai import OpenAI
import openai
import anthropic

import os
import tempfile
import re
import json
import time
import logging
from typing import Dict, List, Any, Tuple, Optional

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ============================================================================
# GLOBAL CACHES (for performance)
# ============================================================================

_cached_intents = None
_cached_layouts = {}  # Per presentation_id
_cached_templates = {}  # Per intent_type

# ============================================================================
# LAZY IMPORTS
# ============================================================================

_psycopg2 = None
_service_account = None
_build = None

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

# ============================================================================
# DATABASE CONNECTION
# ============================================================================

def get_db_connection():
    """Get PostgreSQL database connection"""
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL environment variable not set")

    psycopg2 = get_psycopg2()
    return psycopg2.connect(database_url)

# ============================================================================
# DATABASE - ERROR TRACKING
# ============================================================================

def init_error_table():
    """Initialize error tracking table"""
    conn = None
    cur = None
    try:
        conn = get_db_connection()
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
    except Exception as e:
        logging.error(f"Error initializing error table: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def record_error(presentation_id: str, error_code: str, error_msg: str):
    """Record error to database"""
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO slide_errors (presentation_id, error_code, error_msg) VALUES (%s, %s, %s)",
            (presentation_id, error_code, error_msg)
        )
        conn.commit()
    except Exception as e:
        logging.warning(f"Could not record error: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def record_fix(presentation_id: str, error_code: str, correct_code: str):
    """Record successful fix to database"""
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE slide_errors SET correct_code = %s WHERE presentation_id = %s AND error_code = %s AND correct_code IS NULL",
            (correct_code, presentation_id, error_code)
        )
        conn.commit()
    except Exception as e:
        logging.warning(f"Could not record fix: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# ============================================================================
# DATABASE - PRESENTATION TRACKING
# ============================================================================

def save_presentation(presentation_id: str, title: str, instructions: str, 
                     email: str = None, started_at: float = None):
    """Save presentation metadata"""
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        if email:
            # Check if record exists first to decide between INSERT or UPDATE if needed, 
            # but ON CONFLICT handles it. We just need to ensure title/instructions aren't overwritten with empty strings
            cur.execute(
                """INSERT INTO presentations 
                   (presentation_id, presentation_title, instructions_text, email_address, 
                    presentation_creation_started_at, presentation_created_at)
                   VALUES (%s, %s, %s, %s, %s, NOW())
                   ON CONFLICT (presentation_id) DO UPDATE SET 
                    email_address = EXCLUDED.email_address,
                    presentation_title = CASE WHEN EXCLUDED.presentation_title != '' THEN EXCLUDED.presentation_title ELSE presentations.presentation_title END,
                    instructions_text = CASE WHEN EXCLUDED.instructions_text != '' THEN EXCLUDED.instructions_text ELSE presentations.instructions_text END""",
                (presentation_id, title or "", instructions or "", email, started_at)
            )
        else:
            cur.execute(
                """INSERT INTO presentations 
                   (presentation_id, presentation_title, instructions_text, 
                    presentation_creation_started_at, presentation_created_at)
                   VALUES (%s, %s, %s, %s, NOW())
                   ON CONFLICT (presentation_id) DO NOTHING""",
                (presentation_id, title, instructions, started_at)
            )
        conn.commit()
    except Exception as e:
        logging.error(f"Error saving presentation: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def mark_completed(presentation_id: str) -> Optional[float]:
    """Mark presentation complete and return total seconds"""
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """UPDATE presentations SET presentation_completed_at = NOW()
               WHERE presentation_id = %s
               RETURNING EXTRACT(EPOCH FROM (presentation_completed_at - presentation_creation_started_at))""",
            (presentation_id,)
        )
        result = cur.fetchone()
        conn.commit()
        return result[0] if result else None
    except Exception as e:
        logging.error(f"Error marking presentation as completed: {e}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# ============================================================================
# DATABASE - INTENTS (CACHED)
# ============================================================================

def get_all_intents() -> List[Dict]:
    """Get all intents from database (cached)"""
    global _cached_intents

    if _cached_intents is not None:
        return _cached_intents

    conn = None
    cur = None
    try:
        conn = get_db_connection()
        psycopg2 = get_psycopg2()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT intent_type, description FROM intent_to_api ORDER BY intent_type")
        _cached_intents = [dict(row) for row in cur.fetchall()]
        logging.info(f"✓ Loaded {len(_cached_intents)} intents (cached)")
        return _cached_intents
    except Exception as e:
        logging.error(f"Error fetching intents: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

def get_templates_batch(intent_types: List[str]) -> Dict[str, Any]:
    """Get multiple templates in one query (optimized)"""
    global _cached_templates

    # Check cache first
    missing = [t for t in intent_types if t not in _cached_templates]

    if not missing:
        return {t: _cached_templates[t] for t in intent_types}

    # Fetch missing templates
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        psycopg2 = get_psycopg2()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT intent_type, api_template FROM intent_to_api WHERE intent_type = ANY(%s)",
            (missing,)
        )

        for row in cur.fetchall():
            _cached_templates[row['intent_type']] = row['api_template']

        return {t: _cached_templates[t] for t in intent_types if t in _cached_templates}
    except Exception as e:
        logging.error(f"Error fetching templates batch: {e}")
        return {}
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

# ============================================================================
# AUDIO PROCESSING
# ============================================================================

def convert_audio_segment_to_wav(audio_segment, sample_rate=16000):
    """Convert AudioSegment to WAV file and return path"""
    # Set the sample rate if it's different from the original
    if audio_segment.frame_rate != sample_rate:
        audio_segment = audio_segment.set_frame_rate(sample_rate)

    # Create a temporary WAV file
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav_file:
        # Export audio directly to the WAV file
        audio_segment.export(tmp_wav_file.name, format="wav")
        wav_path = tmp_wav_file.name

    return wav_path

def transcribe_audio(wav_path):
    """Transcribe WAV file using OpenAI Whisper API"""
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    with open(wav_path, "rb") as wav_file:
        try:
            response = client.audio.transcriptions.create(
                model="whisper-1", 
                file=wav_file, 
                response_format="text"
            )
            return response
        except Exception as e:
            logging.error(f"Transcription failed: {e}")
            raise Exception(f"Transcription failed: {str(e)}")

def share_presentation(presentation_id: str, email: str):
    """Share presentation with an email address"""
    _, build = get_google_services()
    drive_service = build('drive', 'v3', credentials=credentials)
    
    try:
        drive_service.permissions().create(
            fileId=presentation_id,
            body={
                'type': 'user',
                'role': 'writer',
                'emailAddress': email
            }
        ).execute()
        logging.info(f"✓ Shared {presentation_id} with {email}")
        
        # Update database with email
        save_presentation(presentation_id, "", "", email)
    except Exception as e:
        logging.error(f"Error sharing presentation: {e}")
        raise e

def get_credentials():
    """Get Google service account credentials"""
    service_account_json = os.getenv('SERVICE_ACCOUNT_PATH')
    if not service_account_json:
        raise ValueError("SERVICE_ACCOUNT_PATH environment variable not set")

    service_account, _ = get_google_services()
    service_account_info = json.loads(service_account_json)

    scopes = [
        'https://www.googleapis.com/auth/presentations',
        'https://www.googleapis.com/auth/drive'
    ]

    return service_account.Credentials.from_service_account_info(
        service_account_info, scopes=scopes
    )

credentials = get_credentials()

# ============================================================================
# ANTHROPIC CLIENT
# ============================================================================

def get_claude_client():
    """Get Anthropic Claude client"""
    api_key = os.getenv('CLAUDE_API_KEY')
    if not api_key:
        raise ValueError("CLAUDE_API_KEY environment variable not set")
    return anthropic.Anthropic(api_key=api_key)

claude_client = get_claude_client()

# ============================================================================
# PRESENTATION CREATION
# ============================================================================

def create_presentation(instructions: str, started_at: float = None) -> Tuple:
    """Create presentation and decide template usage"""
    _, build = get_google_services()
    slides_service = build('slides', 'v1', credentials=credentials)
    drive_service = build('drive', 'v3', credentials=credentials)

    # Get title and template decision from LLM
    prompt = """Return JSON with presentation title and whether to use template:
{
  "title": "clear_concise_title",
  "use_template": true_or_false,
  "theme": {
    "primary_color": {"red": 0.1, "green": 0.1, "blue": 0.3},
    "secondary_color": {"red": 0.9, "green": 0.9, "blue": 0.9},
    "font_family": "Roboto"
  }
}

Set use_template to true if NO specific design instructions (colors/fonts) are given.
If use_template is false, provide a professional color theme and font.
Return ONLY the JSON."""

    response = claude_client.messages.create(
        model="claude-opus-4-20250514",
        max_tokens=500,
        temperature=0.5,
        system=prompt,
        messages=[{"role": "user", "content": instructions}]
    )

    try:
        text = response.content[0].text
        # Clean control characters before parsing
        text = re.sub(r'[\x00-\x1F\x7F]', '', text)
        cleaned = re.sub(r'^```.*\n?|```$', '', text, flags=re.MULTILINE)
        result = json.loads(cleaned, strict=False)
        title = result.get("title", "SlideMakr Presentation")
        use_template = result.get("use_template", True)
        theme = result.get("theme", {})
    except Exception as e:
        logging.error(f"Error parsing presentation setup: {e}")
        # Try a more aggressive cleanup if simple one failed
        try:
            cleaned = "".join(ch for ch in cleaned if ord(ch) >= 32 or ch in "\n\r\t")
            result = json.loads(cleaned, strict=False)
            title = result.get("title", "SlideMakr Presentation")
            use_template = result.get("use_template", True)
            theme = result.get("theme", {})
        except:
            title = "SlideMakr Presentation"
            use_template = True
            theme = {}

    # Create presentation
    template_id = os.getenv('SLIDE_TEMPLATE_ID')

    if use_template and template_id:
        presentation = drive_service.files().copy(
            fileId=template_id,
            body={'name': title}
        ).execute()
        presentation_id = presentation['id']
    else:
        presentation = slides_service.presentations().create(
            body={'title': title}
        ).execute()
        presentation_id = presentation['presentationId']
        
        # Apply basic styling for blank presentations
        if theme:
            requests = [
                {
                    "updateTextStyle": {
                        "objectId": "p", # Standard ID for title
                        "style": {
                            "foregroundColor": {"opaqueColor": {"rgbColor": theme.get("primary_color")}},
                            "fontFamily": theme.get("font_family", "Arial")
                        },
                        "fields": "foregroundColor,fontFamily",
                        "textRange": {"type": "ALL"}
                    }
                }
            ]
            # Note: We'll let the intent generator handle specific object styling
            # but this sets the stage.

    save_presentation(presentation_id, title, instructions, None, started_at)

    return slides_service, presentation_id, title, use_template, theme

# ============================================================================
# LAYOUT & OBJECT FETCHING
# ============================================================================

def get_layouts(service, presentation_id: str) -> Dict[str, Any]:
    """Get available layouts (cached per presentation)"""
    global _cached_layouts

    if presentation_id in _cached_layouts:
        return _cached_layouts[presentation_id]

    presentation = service.presentations().get(
        presentationId=presentation_id,
        fields='layouts'
    ).execute()

    layouts = {}
    for layout in presentation.get('layouts', []):
        name = layout['layoutProperties']['name']

        placeholders = []
        for element in layout.get('pageElements', []):
            if 'shape' in element and 'placeholder' in element['shape']:
                ph = element['shape']['placeholder']
                placeholders.append({
                    'type': ph.get('type'),
                    'index': ph.get('index')
                })

        placeholder_types = [p['type'] for p in placeholders]
        description = f"Has {len(placeholders)} placeholder(s)"
        if placeholder_types:
            description += f": {', '.join(placeholder_types)}"

        layouts[name] = {
            'displayName': layout['layoutProperties'].get('displayName', name),
            'placeholders': placeholders,
            'description': description
        }

    _cached_layouts[presentation_id] = layouts
    logging.info(f"✓ Loaded {len(layouts)} layouts (cached)")
    return layouts

def get_slide_objects(service, presentation_id: str, slide_id: str) -> List[Dict]:
    """Get all objects on a slide"""
    page = service.presentations().pages().get(
        presentationId=presentation_id,
        pageObjectId=slide_id
    ).execute()

    objects = []
    for element in page.get('pageElements', []):
        obj = {'objectId': element.get('objectId'), 'type': None}

        if 'shape' in element:
            obj['type'] = 'shape'
            obj['shapeType'] = element['shape'].get('shapeType')
            if 'placeholder' in element['shape']:
                obj['placeholder'] = element['shape']['placeholder'].get('type')
        elif 'table' in element:
            obj['type'] = 'table'
        elif 'image' in element:
            obj['type'] = 'image'
        elif 'video' in element:
            obj['type'] = 'video'
        elif 'line' in element:
            obj['type'] = 'line'

        objects.append(obj)

    return objects

def get_all_slide_objects(service, presentation_id: str) -> Dict[str, List]:
    """Get objects for all slides"""
    presentation = service.presentations().get(presentationId=presentation_id).execute()

    all_objects = {}
    for slide in presentation.get('slides', []):
        slide_id = slide['objectId']
        all_objects[slide_id] = get_slide_objects(service, presentation_id, slide_id)

    return all_objects

# ============================================================================
# SYSTEM PROMPT
# ============================================================================

def build_prompt(layouts: Dict, slide_objects: Dict, intents: List[Dict]) -> str:
    """Build system prompt for LLM"""

    # Format layouts
    layout_list = "\n".join([f"  - {name}: {info['description']}" 
                             for name, info in layouts.items()])

    # Format intents
    intent_list = "\n".join([f"  - {i['intent_type']}: {i['description']}" 
                             for i in intents])

    # Format existing objects
    object_list = []
    for slide_id, objects in slide_objects.items():
        object_list.append(f"  - Slide '{slide_id}':")
        for obj in objects:
            detail = f"      {obj['objectId']} (type: {obj.get('type', 'unknown')}"
            if 'placeholder' in obj:
                detail += f", placeholder: {obj['placeholder']}"
            detail += ")"
            object_list.append(detail)
    object_section = "\n".join(object_list)

    # Corrected Intent Logic: Clarifying object creation and connections
    return f"""You are an expert Google Slides API designer. Translate user instructions into intents.

Instead of writing API code yourself, choose the INTENT that maps to the operation you want.
Example: To create a slide, choose the "createSlide" intent.

AVAILABLE INTENTS ({len(intents)} operations):
{intent_list}

AVAILABLE LAYOUTS (use ONLY these):
{layout_list}

EXISTING SLIDE OBJECTS:
{object_section}

RULES:

1. SLIDE CREATION:
   - Use "createSlide" with SLIDE_ID like "slide_2", "slide_3".
   - INDEX is 0-based.
   - LAYOUT must be one of the AVAILABLE LAYOUTS (e.g., "TITLE_AND_BODY").

2. OBJECT IDS & PLACEHOLDERS:
   - When using "createSlide", you can optionally provide "placeholderIdMappings" in the API template (if supported by the intent) OR simply use the "OBJECT_ID" from EXISTING SLIDE OBJECTS if you are updating an existing slide.
   - For NEW slides, use the system's generated IDs for placeholders (e.g., "i0", "i1") which you can find by checking the layout's placeholders.
   - If you need to create a custom shape, use "createShape" with a unique SHAPE_ID.

3. TEXT INSERTION:
   - Use "insertText" to put text into a shape or placeholder.
   - The OBJECT_ID must exist! Check EXISTING SLIDE OBJECTS.
   - If you just created a slide, wait for the slide to be created before inserting text, but since we batch, ensure the OBJECT_ID you refer to is either an existing one or one you are creating in this batch with a specific ID.

4. FLOWCHARTS & DIAGRAMS:
   - Use "createShape" for boxes (SHAPE_TYPE: "RECTANGLE", "DIAMOND", etc.).
   - Use "createLine" for connectors.
   - CATEGORY for createLine: "STRAIGHT", "BENT", "CURVED".
   - Use "updateLineProperties" to add arrows: "endArrow": "STEALTH_ARROW".

5. EXECUTION ORDER:
   - Use "order" field to control sequence.
   - Create object (order N) -> Insert text (order N+1).

6. OUTPUT FORMAT:
   Return ONLY a JSON array of intents. NO markdown, NO explanation.
   [
     {{
       "intent": "createSlide",
       "parameters": {{ "SLIDE_ID": "slide_2", "INDEX": "1", "LAYOUT": "TITLE_AND_BODY" }},
       "order": 1
     }},
     {{
       "intent": "insertText",
       "parameters": {{ "OBJECT_ID": "i0", "TEXT": "Slide Title", "INSERTION_INDEX": "0" }},
       "order": 2
     }}
   ]"""

# ============================================================================
# JSON VALIDATION
# ============================================================================

def parse_json_response(response_text: str) -> List[Dict]:
    """Parse and validate JSON response from LLM"""
    # Remove markdown
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', response_text, flags=re.MULTILINE).strip()
    
    # Use raw string for control character cleaning to avoid any double-escaping issues
    # specifically targeting \x00-\x1F (control chars) but allowing \n, \r, \t
    # wait, the error is likely in a STRING literal in the JSON that has a real newline or something
    # json.loads is very picky about control characters.
    
    # Let's use a more robust cleaning that handles escaped sequences too
    # The error "Invalid control character" in json.loads usually means a literal control character
    # like a newline \n inside a string "...", which should be escaped as \\n.
    
    # Attempt to escape unescaped control characters in strings
    # This is complex, but often the issue is literal newlines or tabs in strings.
    # A simpler approach: replace literal control characters with their escaped versions
    # but ONLY if they are inside double quotes? No, simpler to just strip them if they are truly invalid
    
    # Strip literal control characters except \n \r \t which are often okay if handled by a pre-processor
    # but json.loads hates literal \n in strings.
    cleaned = "".join(ch for ch in cleaned if ord(ch) >= 32 or ch in "\n\r\t")
    
    # Extract array
    if not cleaned.startswith('['):
        start = cleaned.find('[')
        end = cleaned.rfind(']')
        if start == -1 or end == -1:
            raise ValueError(f"No JSON array found. Starts with: {response_text[:100]}")
        cleaned = cleaned[start:end+1]

    # Final attempt to clean literal newlines within quotes which are common "invalid control character" culprits
    # We'll replace literal newlines with \n escape sequence if they appear between quotes
    # But a safer bet for "Invalid control character at char X" is often just a literal \t or \n that shouldn't be there.
    # Let's use strict=False in json.loads if available, but it's not always supported.
    # In Python 3, json.loads has a 'strict' parameter.
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError as e:
        # If strict=False still fails, try one more aggressive cleanup
        cleaned_v2 = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', cleaned)
        try:
            return json.loads(cleaned_v2, strict=False)
        except:
            raise ValueError(f"Invalid JSON: {e}\nAttempted: {cleaned[:500]}")

# ============================================================================
# INTENT GENERATION
# ============================================================================

def generate_intents(instructions: str, layouts: Dict, slide_objects: Dict, 
                     intents: List[Dict], max_retries: int = 3) -> List[Dict]:
    """Generate all intents with retry logic"""

    system_prompt = build_prompt(layouts, slide_objects, intents)
    user_message = f"{instructions}\n\nGenerate ALL intents (slides + content). Use 'order' field to control execution."

    for attempt in range(max_retries):
        try:
            response = claude_client.messages.create(
                model="claude-opus-4-20250514",
                max_tokens=20000,
                temperature=0.7,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}]
            )

            if response.content and hasattr(response.content[0], 'text'):
                return parse_json_response(response.content[0].text)
            else:
                raise ValueError("Empty response from Claude")

        except ValueError as e:
            logging.error(f"Validation failed (attempt {attempt + 1}): {e}")

            if attempt < max_retries - 1:
                user_message = f"Previous response had error: {e}\n\nProvide corrected JSON. Rules:\n1. ONLY JSON array\n2. NO markdown\n3. Valid syntax\n\nOriginal: {instructions}"
            else:
                raise ValueError(f"Failed after {max_retries} attempts: {e}")
        except Exception as e:
            logging.error(f"Error generating intents (attempt {attempt + 1}): {e}")
            if attempt >= max_retries - 1:
                raise e

    return []

# ============================================================================
# UTILITIES
# ============================================================================

def get_error_stats():
    """Get statistics from slide_errors table"""
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        psycopg2 = get_psycopg2()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM slide_errors ORDER BY created_at DESC LIMIT 100")
        return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        logging.error(f"Error getting error stats: {e}")
        return []
    finally:
        if cur: cur.close()
        if conn: conn.close()

def reset_error_db():
    """Clear all records from slide_errors table"""
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM slide_errors")
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error resetting error db: {e}")
        return False
    finally:
        if cur: cur.close()
        if conn: conn.close()

# Make attributes available for server.py backward compatibility
code_client = claude_client
template_id = os.getenv('SLIDE_TEMPLATE_ID')

def run_intent_based_requests(instructions: str, presentation_id: str, 
                              service, use_template: bool):
    """Generate and execute intents for a presentation"""
    intents_db = get_all_intents()
    layouts = get_layouts(service, presentation_id)
    slide_objects = get_all_slide_objects(service, presentation_id)
    
    # Generate intents
    intents = generate_intents(instructions, layouts, slide_objects, intents_db)
    
    # Build requests
    requests = build_requests(intents, presentation_id)
    
    # Execute
    return execute_batch(service, presentation_id, requests, intents_db)

def build_requests(intents: List[Dict], presentation_id: str) -> List[Dict]:
    """Convert intents to API requests using batched template fetching"""

    # Get all unique intent types
    intent_types = list(set(i['intent'] for i in intents))

    # Batch fetch templates (1 query instead of N)
    templates = get_templates_batch(intent_types)

    # Sort by order
    sorted_intents = sorted(intents, key=lambda x: x.get('order', 0))

    requests = []
    for intent_data in sorted_intents:
        intent_type = intent_data['intent']
        parameters = intent_data['parameters']

        if intent_type not in templates:
            logging.warning(f"Unknown intent '{intent_type}', skipping")
            continue

        # Fill template
        template_str = json.dumps(templates[intent_type])

        for key, value in parameters.items():
            placeholder = f"{{{{{key}}}}}"

            if isinstance(value, (dict, list)):
                template_str = template_str.replace(f'"{placeholder}"', json.dumps(value))
            elif isinstance(value, bool):
                template_str = template_str.replace(f'"{placeholder}"', str(value).lower())
            elif isinstance(value, (int, float)):
                template_str = template_str.replace(f'"{placeholder}"', str(value))
            else:
                template_str = template_str.replace(placeholder, str(value))

        requests.append(json.loads(template_str))

    return requests

# ============================================================================
# RETRY WITH FRESH CONTEXT
# ============================================================================

def retry_request(service, presentation_id: str, failed_request: Dict, 
                 error_msg: str, intents: List[Dict]) -> Optional[Dict]:
    """Retry failed request with fresh context"""

    # Get fresh context (layouts cached, objects fresh)
    layouts = get_layouts(service, presentation_id)
    slide_objects = get_all_slide_objects(service, presentation_id)

    prompt = f"""This request failed:
{json.dumps(failed_request, indent=2)}

Error: {error_msg}

FRESH LAYOUTS:
{json.dumps(layouts, indent=2)}

FRESH OBJECTS:
{json.dumps(slide_objects, indent=2)}

INTENTS:
{json.dumps([{"intent": i["intent_type"]} for i in intents], indent=2)}

Return corrected intent. Consider:
- Using wrong objectId? Use one from FRESH OBJECTS
- Using invalid layout? Use one from FRESH LAYOUTS
- Creating object after referencing? Fix order

Return ONLY JSON array with corrected intent:
[{{"intent": "...", "parameters": {{...}}, "order": 1}}]

NO markdown, NO explanation."""

    try:
        response = claude_client.messages.create(
            model="claude-opus-4-20250514",
            max_tokens=5000,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}]
        )

        fixed_intents = parse_json_response(response.content[0].text)
        if not fixed_intents:
            return None

        fixed_requests = build_requests(fixed_intents, presentation_id)
        return fixed_requests[0] if fixed_requests else None

    except Exception as e:
        logging.error(f"Retry generation failed: {e}")
        return None

# ============================================================================
# MAIN CREATION FLOW
# ============================================================================

def create_presentation_optimized(instructions: str, email: str = None) -> Tuple[str, Dict]:
    """
    Optimized presentation creation flow:
    1. Create presentation + get first slide
    2. Get context (layouts cached, intents cached)
    3. Generate ALL intents in one call
    4. Build API requests (batched template fetch)
    5. Execute all requests
    6. Retry failures with fresh context
    7. Done
    """

    started_at = time.time()

    logging.info("="*70)
    logging.info("CREATING PRESENTATION")
    logging.info("="*70)

    # Phase 1: Setup
    logging.info("\n[1] Setup...")
    service, presentation_id, title, use_template = create_presentation(instructions, started_at)
    logging.info(f"✓ {title} ({presentation_id})")

    # Get first slide
    presentation = service.presentations().get(presentationId=presentation_id).execute()
    first_slide_id = presentation['slides'][0]['objectId']
    first_slide_objects = get_slide_objects(service, presentation_id, first_slide_id)
    logging.info(f"✓ First slide: {len(first_slide_objects)} objects")

    # Phase 2: Get context
    logging.info("\n[2] Loading context...")
    layouts = get_layouts(service, presentation_id)
    intents_db = get_all_intents()
    all_slide_objects = get_all_slide_objects(service, presentation_id)

    # Phase 3: Generate intents
    logging.info("\n[3] Generating intents...")
    all_intents = generate_intents(
        instructions, 
        layouts, 
        all_slide_objects, 
        intents_db
    )
    logging.info(f"✓ {len(all_intents)} intents")

    # Phase 4: Build requests
    logging.info("\n[4] Building API requests...")
    requests = build_requests(all_intents, presentation_id)
    logging.info(f"✓ {len(requests)} requests")

    # Phase 5: Execute
    logging.info("\n[5] Executing...")
    failed = []
    success = 0

    for i, req in enumerate(requests):
        try:
            service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={'requests': [req]}
            ).execute()
            success += 1
            if (i + 1) % 5 == 0:
                logging.info(f"  {i + 1}/{len(requests)}...")
        except Exception as e:
            logging.error(f"  Request {i + 1} failed: {e}")
            record_error(presentation_id, json.dumps(req), str(e))
            failed.append({'request': req, 'error': str(e)})

    logging.info(f"✓ {success}/{len(requests)} successful")

    # Phase 6: Retry failures
    errors = {}
    if failed:
        logging.info(f"\n[6] Retrying {len(failed)} failures...")
        retry_success = 0

        for failure in failed:
            corrected = retry_request(
                service, presentation_id, 
                failure['request'], failure['error'], 
                intents_db
            )

            if corrected:
                try:
                    service.presentations().batchUpdate(
                        presentationId=presentation_id,
                        body={'requests': [corrected]}
                    ).execute()
                    record_fix(presentation_id, json.dumps(failure['request']), json.dumps(corrected))
                    retry_success += 1
                except Exception as e:
                    errors[json.dumps(failure['request'])] = str(e)
            else:
                errors[json.dumps(failure['request'])] = failure['error']

        logging.info(f"✓ {retry_success}/{len(failed)} fixed")
    else:
        logging.info("\n[6] No failures - skipping retry")

    # Phase 7: Finalize
    logging.info("\n[7] Finalizing...")
    total_seconds = mark_completed(presentation_id)

    if email:
        _, build = get_google_services()
        drive = build('drive', 'v3', credentials=credentials)
        drive.permissions().create(
            fileId=presentation_id,
            body={'type': 'user', 'role': 'writer', 'emailAddress': email},
            fields='id'
        ).execute()
        logging.info(f"✓ Shared with {email}")

    logging.info("\n" + "="*70)
    logging.info("COMPLETE")
    logging.info("="*70)
    logging.info(f"Time: {total_seconds:.1f}s")
    logging.info(f"Success: {success + len(failed) - len(errors)}/{len(requests)}")
    if errors:
        logging.warning(f"Errors: {len(errors)}")

    url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
    return url, errors

# ============================================================================
# AUDIO FUNCTIONS (kept from original)
# ============================================================================

def record_audio(threshold=30, silence_duration=4):
    """Record audio until silence"""
    import pyaudio
    import wave

    CHUNK = 1024
    FORMAT = pyaudio.paFloat32
    CHANNELS = 1
    RATE = 44100

    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, 
                    input=True, frames_per_buffer=CHUNK)

    print("Recording...")
    frames = []
    silence_start = None

    try:
        while True:
            data = stream.read(CHUNK)
            frames.append(data)
            audio_data = np.frombuffer(data, dtype=np.float32)
            volume = np.linalg.norm(audio_data) * 10

            if volume < threshold:
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

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wf = wave.open(f.name, 'wb')
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(p.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
        wf.close()
        return f.name

def transcribe_audio(audio_file: str) -> str:
    """Transcribe audio using OpenAI Whisper"""
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    with open(audio_file, "rb") as f:
        return client.audio.transcriptions.create(
            model="whisper-1", 
            file=f, 
            response_format="text"
        )

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main(instructions: str, email: str = None):
    """Main entry point"""
    init_error_table()
    return create_presentation_optimized(instructions, email)

if __name__ == "__main__":
    test_instructions = """
    Make a presentation about why Christina would be great for the A team.
    She surprises people by building exciting things. Like SlideMaker.
    She's great at understanding problems and creating solutions.
    End with a photo showing determination.
    """

    url, errors = main(test_instructions)
    print(f"\n✓ Presentation: {url}")
    if errors:
        print(f"⚠ Errors: {len(errors)}")