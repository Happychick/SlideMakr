"""
SlideMakr - Google Slides & Drive API Operations

Extracted and adapted from the existing SlideMakr codebase.
Handles all direct Google API interactions:
- Presentation creation (blank or from template)
- Slide object reading (for editing context)
- Batch update execution with per-request error isolation
- Presentation sharing via Drive API
"""

import os
import io
import json
import logging
import time
from typing import Dict, List, Any, Tuple, Optional

import requests as http_requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from .slides_schema import validate_requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ============================================================================
# CREDENTIALS & SERVICE SETUP
# ============================================================================

SCOPES = [
    'https://www.googleapis.com/auth/presentations',
    'https://www.googleapis.com/auth/drive'
]


def get_credentials():
    """Get Google service account credentials.

    Supports two modes:
    - SERVICE_ACCOUNT_PATH as a file path (local dev)
    - SERVICE_ACCOUNT_JSON as raw JSON string (Cloud Run / Replit)
    """
    # Try file path first (local development)
    sa_path = os.getenv('SERVICE_ACCOUNT_PATH')
    if sa_path and os.path.isfile(sa_path):
        return service_account.Credentials.from_service_account_file(
            sa_path, scopes=SCOPES
        )

    # Try JSON string (Cloud Run / Replit)
    sa_json = os.getenv('SERVICE_ACCOUNT_JSON') or os.getenv('SERVICE_ACCOUNT_PATH')
    if sa_json:
        try:
            info = json.loads(sa_json)
            return service_account.Credentials.from_service_account_info(
                info, scopes=SCOPES
            )
        except (json.JSONDecodeError, ValueError):
            pass

    raise ValueError(
        "No valid service account found. Set SERVICE_ACCOUNT_PATH (file) "
        "or SERVICE_ACCOUNT_JSON (JSON string)."
    )


def get_slides_service():
    """Get Google Slides API service."""
    creds = get_credentials()
    return build('slides', 'v1', credentials=creds)


def get_drive_service():
    """Get Google Drive API service."""
    creds = get_credentials()
    return build('drive', 'v3', credentials=creds)


# ============================================================================
# PRESENTATION CREATION
# ============================================================================

def create_presentation(title: str, use_template: bool = False) -> Tuple[str, str]:
    """Create a new Google Slides presentation.

    Args:
        title: Presentation title
        use_template: Whether to copy from template (requires SLIDE_TEMPLATE_ID env var)

    Returns:
        Tuple of (presentation_id, presentation_url)
    """
    slides_service = get_slides_service()
    drive_service = get_drive_service()
    template_id = os.getenv('SLIDE_TEMPLATE_ID')

    # Use template by default when available
    if template_id:
        try:
            presentation = drive_service.files().copy(
                fileId=template_id,
                body={'name': title}
            ).execute()
            presentation_id = presentation['id']
            logging.info(f"Created from template: {template_id}")
        except Exception as e:
            logging.warning(f"Template copy failed, creating blank: {e}")
            presentation = slides_service.presentations().create(
                body={'title': title}
            ).execute()
            presentation_id = presentation['presentationId']
    else:
        # No template — create blank
        presentation = slides_service.presentations().create(
            body={'title': title}
        ).execute()
        presentation_id = presentation['presentationId']

    # Make the presentation viewable by anyone with the link (for preview iframe)
    try:
        drive_service.permissions().create(
            fileId=presentation_id,
            body={'type': 'anyone', 'role': 'reader'},
            fields='id'
        ).execute()
        logging.info(f"Set presentation to link-viewable: {presentation_id}")
    except Exception as e:
        logging.warning(f"Could not set link sharing: {e}")

    url = f'https://docs.google.com/presentation/d/{presentation_id}/edit'
    logging.info(f"Created presentation: {title} ({presentation_id})")
    return presentation_id, url


# ============================================================================
# TEMPLATE LAYOUTS
# ============================================================================

def get_template_layouts(presentation_id: str) -> List[Dict[str, Any]]:
    """Get available slide layouts from a presentation's template.

    Returns layout IDs and names that can be used with createSlide.
    Also returns placeholder info so the agent knows which elements
    exist in each layout (TITLE, SUBTITLE, BODY, etc.).

    Args:
        presentation_id: Google Slides presentation ID

    Returns:
        List of layout dicts with objectId, name, and placeholders
    """
    slides_service = get_slides_service()

    presentation = slides_service.presentations().get(
        presentationId=presentation_id
    ).execute()

    layouts = []
    for layout in presentation.get('layouts', []):
        layout_data = {
            'objectId': layout['objectId'],
            'name': layout.get('layoutProperties', {}).get('displayName', 'Unknown'),
            'placeholders': []
        }

        for element in layout.get('pageElements', []):
            if 'shape' in element and 'placeholder' in element['shape']:
                ph = element['shape']['placeholder']
                layout_data['placeholders'].append({
                    'type': ph.get('type', 'NONE'),
                    'index': ph.get('index', 0),
                })

        layouts.append(layout_data)

    logging.info(f"Found {len(layouts)} layouts in presentation {presentation_id}")
    return layouts


# ============================================================================
# PRESENTATION STATE (for editing)
# ============================================================================

def get_presentation_state(presentation_id: str) -> Dict[str, Any]:
    """Get the full state of a presentation for editing context.

    Returns structured data about all slides, their elements, text content,
    and properties. This gives the agent enough context to know what exists
    and how to modify it.

    Args:
        presentation_id: Google Slides presentation ID

    Returns:
        Dict with slides, their objects, text content, and metadata
    """
    slides_service = get_slides_service()

    presentation = slides_service.presentations().get(
        presentationId=presentation_id
    ).execute()

    state = {
        'title': presentation.get('title', ''),
        'presentation_id': presentation_id,
        'slide_count': len(presentation.get('slides', [])),
        'slides': []
    }

    for slide_index, slide in enumerate(presentation.get('slides', [])):
        slide_id = slide['objectId']
        slide_data = {
            'slide_id': slide_id,
            'slide_index': slide_index,
            'elements': []
        }

        for element in slide.get('pageElements', []):
            elem_data = {
                'objectId': element.get('objectId'),
                'type': _get_element_type(element),
            }

            # Extract size and position
            if 'size' in element:
                elem_data['size'] = element['size']
            if 'transform' in element:
                elem_data['transform'] = element['transform']

            # Extract text content
            if 'shape' in element:
                shape = element['shape']
                elem_data['shapeType'] = shape.get('shapeType')

                if 'placeholder' in shape:
                    elem_data['placeholder'] = shape['placeholder'].get('type')

                if 'text' in shape:
                    text_content = _extract_text(shape['text'])
                    if text_content:
                        elem_data['text'] = text_content

                # Extract shape properties for styling context
                if 'shapeProperties' in shape:
                    props = shape['shapeProperties']
                    if 'shapeBackgroundFill' in props:
                        elem_data['hasBackground'] = True

            elif 'table' in element:
                table = element['table']
                elem_data['rows'] = table.get('rows', 0)
                elem_data['columns'] = table.get('columns', 0)

            elif 'image' in element:
                elem_data['contentUrl'] = element['image'].get('contentUrl', '')

            slide_data['elements'].append(elem_data)

        state['slides'].append(slide_data)

    return state


def _get_element_type(element: Dict) -> str:
    """Determine the type of a page element."""
    if 'shape' in element:
        return 'shape'
    elif 'table' in element:
        return 'table'
    elif 'image' in element:
        return 'image'
    elif 'video' in element:
        return 'video'
    elif 'line' in element:
        return 'line'
    return 'unknown'


def _extract_text(text_content: Dict) -> str:
    """Extract plain text from a Shape's text content."""
    texts = []
    for element in text_content.get('textElements', []):
        if 'textRun' in element:
            texts.append(element['textRun'].get('content', ''))
    return ''.join(texts).strip()


# ============================================================================
# SLIDE OBJECT READING (for layout context)
# ============================================================================

def get_slide_objects(presentation_id: str, slide_id: str) -> List[Dict]:
    """Get all objects on a specific slide.

    Returns minimal object info needed for intent generation.
    """
    slides_service = get_slides_service()

    page = slides_service.presentations().pages().get(
        presentationId=presentation_id,
        pageObjectId=slide_id
    ).execute()

    objects = []
    for element in page.get('pageElements', []):
        obj = {
            'objectId': element.get('objectId'),
            'type': _get_element_type(element)
        }

        if 'shape' in element:
            obj['shapeType'] = element['shape'].get('shapeType')
            if 'placeholder' in element['shape']:
                obj['placeholder'] = element['shape']['placeholder'].get('type')

        objects.append(obj)

    return objects


def get_all_slide_objects(presentation_id: str) -> Dict[str, List]:
    """Get objects for every slide in the presentation."""
    slides_service = get_slides_service()

    presentation = slides_service.presentations().get(
        presentationId=presentation_id
    ).execute()

    all_objects = {}
    for slide in presentation.get('slides', []):
        slide_id = slide['objectId']
        all_objects[slide_id] = get_slide_objects(presentation_id, slide_id)

    return all_objects


# ============================================================================
# SLIDE THUMBNAILS
# ============================================================================


def get_slide_thumbnail(
    presentation_id: str,
    slide_id: str,
    thumbnail_size: str = "MEDIUM",
) -> Optional[bytes]:
    """Fetch a rendered PNG thumbnail of a specific slide.

    Args:
        presentation_id: Google Slides presentation ID
        slide_id: The objectId of the slide to thumbnail
        thumbnail_size: SMALL (200px), MEDIUM (800px), or LARGE (1600px)

    Returns:
        PNG image bytes, or None on error
    """
    slides_service = get_slides_service()

    try:
        thumbnail = slides_service.presentations().pages().getThumbnail(
            presentationId=presentation_id,
            pageObjectId=slide_id,
            thumbnailProperties_thumbnailSize=thumbnail_size,
        ).execute()

        content_url = thumbnail.get('contentUrl')
        if not content_url:
            return None

        resp = http_requests.get(content_url, timeout=10)
        resp.raise_for_status()
        return resp.content

    except Exception as e:
        logging.error(f"get_slide_thumbnail failed: {e}")
        return None


# ============================================================================
# BATCH UPDATE EXECUTION
# ============================================================================


def execute_slide_requests(
    presentation_id: str,
    requests: List[Dict],
) -> Dict[str, Any]:
    """Execute Google Slides API batch update requests with smart ordering.

    Automatically separates structural requests (createSlide, createShape,
    createTable, createLine, createImage) from content requests (insertText,
    updateTextStyle, etc.) and executes structural ones first as a batch,
    then content ones individually for error isolation.

    Auto-fixes common agent mistakes (wrong color format, wrong field names).

    Args:
        presentation_id: Google Slides presentation ID
        requests: List of Google Slides API request dicts

    Returns:
        Dict with 'success_count', 'total', 'errors', 'url'
    """
    start_time = time.time()

    # Validate and auto-fix requests via Pydantic schema
    requests = validate_requests(requests)

    slides_service = get_slides_service()

    # Separate structural (create) requests from content requests
    STRUCTURAL_TYPES = {
        'createSlide', 'createShape', 'createTable',
        'createLine', 'createImage', 'createVideo',
        'createSheetsChart',
    }

    structural = []
    content = []

    for req in requests:
        req_type = next(iter(req.keys()), '')
        if req_type in STRUCTURAL_TYPES:
            structural.append(req)
        else:
            content.append(req)

    errors = []
    success_count = 0
    total = len(requests)

    # Phase 1: Execute structural requests as a single batch
    # (createSlide order matters, and batching ensures atomicity)
    if structural:
        try:
            slides_service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={'requests': structural}
            ).execute()
            success_count += len(structural)
            logging.info(f"Structural batch: {len(structural)}/{len(structural)} succeeded")
        except Exception as e:
            logging.warning(f"Structural batch failed, falling back to one-by-one: {e}")
            # Fall back to individual execution
            for i, req in enumerate(structural):
                try:
                    slides_service.presentations().batchUpdate(
                        presentationId=presentation_id,
                        body={'requests': [req]}
                    ).execute()
                    success_count += 1
                except Exception as e2:
                    errors.append({
                        'request_index': i,
                        'request': req,
                        'error': str(e2)
                    })
                    logging.error(f"Structural request {i+1} failed: {e2}")

    # Phase 2: Batch content requests in chunks for speed
    # Strip deleteText ALL requests from batch (they fail on empty elements
    # and are no-ops anyway — insertText still works without prior delete on empty elements)
    batch_content = []
    individual_deletes = []
    for req in content:
        req_type = next(iter(req.keys()), '')
        if req_type == 'deleteText':
            tr = req['deleteText'].get('textRange', {})
            if tr.get('type') == 'ALL':
                individual_deletes.append(req)
                continue
        batch_content.append(req)

    # Run deleteText ALL individually first (skip silently if element is empty)
    for req in individual_deletes:
        try:
            slides_service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={'requests': [req]}
            ).execute()
            success_count += 1
        except Exception as e:
            if 'startIndex 0 must be less than the endIndex 0' in str(e):
                logging.info(f"Skipped deleteText on empty element (no-op)")
                success_count += 1
            else:
                errors.append({
                    'request_index': content.index(req),
                    'request': req,
                    'error': str(e)
                })

    # Try remaining content as one batch; fall back to one-by-one on error
    if batch_content:
        try:
            slides_service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={'requests': batch_content}
            ).execute()
            success_count += len(batch_content)
            logging.info(f"Content batch: {len(batch_content)}/{len(batch_content)} succeeded")
        except Exception as e:
            logging.warning(f"Content batch failed, falling back to one-by-one: {e}")
            for i, req in enumerate(batch_content):
                try:
                    slides_service.presentations().batchUpdate(
                        presentationId=presentation_id,
                        body={'requests': [req]}
                    ).execute()
                    success_count += 1
                except Exception as e2:
                    errors.append({
                        'request_index': len(structural) + len(individual_deletes) + i,
                        'request': req,
                        'error': str(e2)
                    })
                    logging.error(f"Content request {i+1}/{len(batch_content)} failed: {e2}")

    if structural or content:
        logging.info(f"Execution complete: {success_count}/{total} succeeded")

    url = f'https://docs.google.com/presentation/d/{presentation_id}/edit'

    execution_time = round(time.time() - start_time, 2)

    result = {
        'success_count': success_count,
        'total': total,
        'url': url,
        'presentation_id': presentation_id,
        'execution_time_seconds': execution_time,
    }

    if errors:
        result['errors'] = errors
        result['error_count'] = len(errors)
        result['status'] = 'partial' if success_count > 0 else 'failed'
    else:
        result['error_count'] = 0
        result['status'] = 'success'

    logging.info(f"execute_slide_requests: {success_count}/{total} in {execution_time}s")
    return result


# ============================================================================
# SHARING
# ============================================================================

def share_presentation(presentation_id: str, email: str) -> Dict[str, str]:
    """Share a presentation with a user via email.

    Args:
        presentation_id: Google Slides presentation ID
        email: Email address to share with

    Returns:
        Dict with status and details
    """
    drive_service = get_drive_service()

    try:
        drive_service.permissions().create(
            fileId=presentation_id,
            body={
                'type': 'user',
                'role': 'writer',
                'emailAddress': email
            },
            fields='id'
        ).execute()

        url = f'https://docs.google.com/presentation/d/{presentation_id}/edit'
        logging.info(f"Shared {presentation_id} with {email}")
        return {
            'status': 'shared',
            'email': email,
            'url': url
        }
    except Exception as e:
        logging.error(f"Error sharing presentation: {e}")
        return {
            'status': 'error',
            'error': str(e)
        }


# ============================================================================
# IMAGE UPLOAD
# ============================================================================

def upload_image_to_drive(image_url: str, filename: str = "slide_image.jpg") -> Optional[str]:
    """Download an image from a URL and upload it to Google Drive.

    Returns a publicly accessible Google Drive URL that can be used with
    Google Slides createImage API.

    Args:
        image_url: The source URL to download the image from
        filename: Filename to use in Drive

    Returns:
        Public Google Drive URL for the uploaded image, or None on failure
    """
    try:
        # Download image
        resp = http_requests.get(image_url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        resp.raise_for_status()

        content_type = resp.headers.get('Content-Type', 'image/jpeg')
        if 'image' not in content_type:
            logging.warning(f"URL did not return an image: {content_type}")
            return None

        # Upload to Drive
        drive_service = get_drive_service()
        file_metadata = {
            'name': filename,
            'mimeType': content_type,
        }
        media = MediaIoBaseUpload(
            io.BytesIO(resp.content),
            mimetype=content_type,
            resumable=False
        )
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

        file_id = file['id']

        # Make the file publicly accessible
        drive_service.permissions().create(
            fileId=file_id,
            body={'type': 'anyone', 'role': 'reader'},
            fields='id'
        ).execute()

        # Return the direct content URL (this format works with Google Slides API)
        drive_url = f"https://drive.google.com/uc?id={file_id}"
        logging.info(f"Uploaded image to Drive: {file_id} from {image_url[:80]}")
        return drive_url

    except Exception as e:
        logging.error(f"Failed to upload image to Drive: {e}")
        return None
