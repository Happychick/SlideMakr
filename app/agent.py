"""
SlideMakr - ADK Agent

Google ADK Agent powered by Gemini that creates and edits Google Slides
presentations from natural language (text or voice).

Tools:
- create_new_presentation: Create a blank or template-based presentation
- execute_slide_requests: Run Google Slides API batchUpdate requests
- get_presentation_state: Read current slide state for editing
- share_presentation_with_user: Share via Drive API
- search_company_branding: Search the web for company brand colors/fonts/logo

The agent generates valid Google Slides API JSON directly via the instruction
prompt — no separate RAG database or nested LLM call needed.
"""

import json
import logging
from typing import Any, Dict, List

from google.adk import Agent
from google import genai
from google.genai import types as genai_types

from . import slidemakr
from . import db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ============================================================================
# TOOL FUNCTIONS (ADK tools = plain Python functions with docstrings)
# ============================================================================


def create_new_presentation(title: str, use_template: bool = False) -> dict:
    """Create a new Google Slides presentation.

    Call this when the user wants to create a new presentation.
    Returns the presentation ID and URL.

    Args:
        title: The title for the new presentation. Choose something clear
               and descriptive based on the user's request.
        use_template: Set to True to use the default template.
                      Set to False (default) if the user wants custom styling
                      or a blank canvas.

    Returns:
        dict with 'presentation_id' and 'url'
    """
    try:
        presentation_id, url = slidemakr.create_presentation(title, use_template)

        # Log to database
        db.save_presentation(
            presentation_id=presentation_id,
            title=title,
            instructions="",
            url=url,
            status="created"
        )

        return {
            'presentation_id': presentation_id,
            'url': url,
            'status': 'success',
            'message': f'Created presentation "{title}"'
        }
    except Exception as e:
        logging.error(f"create_new_presentation failed: {e}")
        return {
            'status': 'error',
            'error': str(e)
        }


def execute_slide_requests(presentation_id: str, requests_json: str) -> dict:
    """Execute Google Slides API batchUpdate requests on a presentation.

    Call this after generating the slide content requests.
    Each request is executed individually so failures are isolated.

    IMPORTANT: The requests_json must be a valid JSON string containing
    an array of Google Slides API request objects.

    Args:
        presentation_id: The Google Slides presentation ID (from create_new_presentation)
        requests_json: A JSON string containing an array of Google Slides API
                       request objects. Example:
                       '[{"createSlide": {"objectId": "slide_1", ...}}, ...]'

    Returns:
        dict with execution results including success count, errors, and URL
    """
    try:
        requests = json.loads(requests_json)
    except json.JSONDecodeError as e:
        return {
            'status': 'error',
            'error': f'Invalid JSON: {str(e)}',
            'hint': 'Ensure requests_json is a valid JSON array of request objects.'
        }

    if not isinstance(requests, list):
        return {
            'status': 'error',
            'error': 'requests_json must be a JSON array',
            'hint': 'Wrap your request objects in an array: [{...}, {...}]'
        }

    result = slidemakr.execute_slide_requests(presentation_id, requests)

    # Log errors to database
    if 'errors' in result:
        for error in result['errors']:
            db.record_error(
                presentation_id=presentation_id,
                request_json=json.dumps(error['request']),
                error_message=error['error']
            )

    # Update presentation status
    db.update_presentation_status(
        presentation_id=presentation_id,
        status=result['status'],
        request_count=result['total']
    )

    return result


def get_presentation_state(presentation_id: str) -> dict:
    """Get the current state of a presentation for editing.

    Call this when the user wants to edit an existing presentation.
    Returns the full state including all slides, their elements,
    text content, and properties so you know exactly what to modify.

    Args:
        presentation_id: The Google Slides presentation ID

    Returns:
        dict with the full presentation state (slides, elements, text, etc.)
    """
    try:
        state = slidemakr.get_presentation_state(presentation_id)
        return {
            'status': 'success',
            'state': state
        }
    except Exception as e:
        logging.error(f"get_presentation_state failed: {e}")
        return {
            'status': 'error',
            'error': str(e)
        }


def share_presentation_with_user(presentation_id: str, email: str) -> dict:
    """Share a presentation with someone via their email address.

    Call this when the user wants to share their presentation.
    Grants editor access to the specified email.

    Args:
        presentation_id: The Google Slides presentation ID
        email: The email address to share the presentation with

    Returns:
        dict with sharing status and URL
    """
    result = slidemakr.share_presentation(presentation_id, email)

    # Update presentation in database
    if result.get('status') == 'shared':
        db.update_presentation_status(
            presentation_id=presentation_id,
            status='shared',
            email=email
        )

    return result


def search_company_branding(company_name: str) -> dict:
    """Search the web for a company's brand guidelines, colors, fonts, and logo.

    Call this when the user mentions a company name and wants the presentation
    styled to match that company's brand. Returns brand colors (as hex and RGB),
    fonts, and logo URL.

    Args:
        company_name: The company name to search for (e.g., "Scale AI", "Stripe", "Airbnb")

    Returns:
        dict with brand info: primary_colors, secondary_colors, fonts, logo_url, summary
    """
    try:
        client = genai.Client()

        prompt = f"""Search for {company_name}'s brand guidelines and visual identity.

Return a structured summary with:
1. **Primary brand colors** — hex codes (e.g., #4A154B) and RGB values (0.0-1.0 scale for each channel)
2. **Secondary/accent colors** — hex codes and RGB values
3. **Brand fonts** — the typefaces they use (headings and body)
4. **Logo URL** — a direct URL to their logo image if available (prefer PNG/SVG on their official site or press kit)
5. **Visual style notes** — any distinctive visual patterns (gradients, dark backgrounds, minimal style, etc.)

Format the colors as both hex AND as RGB on a 0.0-1.0 scale like this:
  #4A154B → red: 0.29, green: 0.08, blue: 0.29

Be specific and accurate. If you can't find exact brand guidelines, use colors visible on their website."""

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
            ),
        )

        return {
            'status': 'success',
            'company': company_name,
            'branding': response.text,
        }
    except Exception as e:
        logging.error(f"search_company_branding failed: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'hint': 'Web search may not be available. Use default styling or ask the user for brand colors.'
        }


# ============================================================================
# AGENT INSTRUCTION PROMPT
# ============================================================================

AGENT_INSTRUCTION = """You are SlideMakr, an AI assistant that creates and edits Google Slides
presentations from natural language instructions. You can work with both voice and text input.

## YOUR CAPABILITIES

You can:
1. **Create** new presentations from scratch
2. **Edit** existing presentations (change text, colors, layout, add/remove slides)
3. **Share** presentations with anyone via email
4. **Brand** presentations to match a company's visual identity (search the web for their brand)

## WORKFLOW

### Creating a New Presentation:
1. Call `create_new_presentation` with a good title
2. Generate the Google Slides API requests as a JSON array
3. Call `execute_slide_requests` with the presentation_id and your requests JSON
4. If any requests fail, examine the errors, fix your requests, and retry
5. Tell the user the presentation URL when done
6. If they want to share it, ask for an email and call `share_presentation_with_user`

### Creating a Branded Presentation (company-themed):
1. If the user mentions a company name (e.g., "make a presentation for Scale AI"),
   call `search_company_branding` with the company name FIRST
2. Use the returned brand colors, fonts, and style to theme ALL slides:
   - Set slide backgrounds using the brand's primary/secondary colors
   - Use brand fonts for all text (fontFamily in updateTextStyle)
   - Use brand accent colors for headings, shapes, and decorative elements
   - If a logo URL is returned, add it to the title slide using createImage
3. Then proceed with normal presentation creation using those brand colors everywhere

### Editing an Existing Presentation:
1. Call `get_presentation_state` to see the current slides, elements, and text
2. Identify which elements need to change (by their objectId)
3. Generate the appropriate update requests
4. Call `execute_slide_requests` with the edit requests
5. Confirm what you changed

## GOOGLE SLIDES API REQUEST REFERENCE

Below are the main request types you can use. Each request is a JSON object
with one key (the request type) and a value (the request body).

### Slide Operations

**createSlide** - Create a new slide:
```json
{
  "createSlide": {
    "objectId": "slide_1",
    "insertionIndex": 1,
    "slideLayoutReference": {
      "predefinedLayout": "TITLE_AND_BODY"
    }
  }
}
```
Available predefinedLayout values:
- BLANK, TITLE, TITLE_AND_BODY, TITLE_AND_TWO_COLUMNS, TITLE_ONLY,
  SECTION_HEADER, SECTION_TITLE_AND_DESCRIPTION, ONE_COLUMN_TEXT,
  MAIN_POINT, BIG_NUMBER, CAPTION_ONLY

**updateSlidesPosition** - Reorder slides:
```json
{
  "updateSlidesPosition": {
    "slideObjectIds": ["slide_1"],
    "insertionIndex": 0
  }
}
```

### Shape Operations

**createShape** - Create a shape (rectangle, circle, text box, etc.):
```json
{
  "createShape": {
    "objectId": "shape_1",
    "shapeType": "TEXT_BOX",
    "elementProperties": {
      "pageObjectId": "slide_1",
      "size": {
        "width": {"magnitude": 3000000, "unit": "EMU"},
        "height": {"magnitude": 500000, "unit": "EMU"}
      },
      "transform": {
        "scaleX": 1, "scaleY": 1,
        "translateX": 500000, "translateY": 500000,
        "unit": "EMU"
      }
    }
  }
}
```
Common shapeTypes: TEXT_BOX, RECTANGLE, ROUND_RECTANGLE, ELLIPSE,
DIAMOND, TRIANGLE, ARROW_NORTH, ARROW_EAST, ARROW_SOUTH, ARROW_WEST

**updateShapeProperties** - Change shape fill, outline, etc.:
```json
{
  "updateShapeProperties": {
    "objectId": "shape_1",
    "shapeProperties": {
      "shapeBackgroundFill": {
        "solidFill": {
          "color": {
            "rgbColor": {"red": 0.2, "green": 0.5, "blue": 0.9}
          }
        }
      }
    },
    "fields": "shapeBackgroundFill.solidFill.color"
  }
}
```

### Text Operations

**insertText** - Insert text into a shape or placeholder:
```json
{
  "insertText": {
    "objectId": "i0",
    "text": "Hello World",
    "insertionIndex": 0
  }
}
```

**deleteText** - Delete text from a shape:
```json
{
  "deleteText": {
    "objectId": "i0",
    "textRange": {
      "type": "ALL"
    }
  }
}
```

**updateTextStyle** - Change text formatting (color, font, size, bold, etc.):
```json
{
  "updateTextStyle": {
    "objectId": "i0",
    "style": {
      "foregroundColor": {
        "opaqueColor": {
          "rgbColor": {"red": 0.0, "green": 0.0, "blue": 1.0}
        }
      },
      "fontSize": {"magnitude": 24, "unit": "PT"},
      "bold": true,
      "fontFamily": "Arial"
    },
    "textRange": {"type": "ALL"},
    "fields": "foregroundColor,fontSize,bold,fontFamily"
  }
}
```

**updateParagraphStyle** - Change paragraph alignment, spacing:
```json
{
  "updateParagraphStyle": {
    "objectId": "i0",
    "style": {
      "alignment": "CENTER",
      "spaceAbove": {"magnitude": 10, "unit": "PT"}
    },
    "textRange": {"type": "ALL"},
    "fields": "alignment,spaceAbove"
  }
}
```

**createParagraphBullets** - Add bullet points:
```json
{
  "createParagraphBullets": {
    "objectId": "i0",
    "textRange": {"type": "ALL"},
    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
  }
}
```

**replaceAllText** - Find and replace text:
```json
{
  "replaceAllText": {
    "containsText": {
      "text": "old text",
      "matchCase": true
    },
    "replaceText": "new text"
  }
}
```

### Table Operations

**createTable** - Create a table:
```json
{
  "createTable": {
    "objectId": "table_1",
    "elementProperties": {
      "pageObjectId": "slide_1",
      "size": {
        "width": {"magnitude": 7000000, "unit": "EMU"},
        "height": {"magnitude": 3000000, "unit": "EMU"}
      },
      "transform": {
        "scaleX": 1, "scaleY": 1,
        "translateX": 1000000, "translateY": 1500000,
        "unit": "EMU"
      }
    },
    "rows": 3,
    "columns": 3
  }
}
```

**insertText (in table cell)** - Add text to a table cell:
```json
{
  "insertText": {
    "objectId": "table_1",
    "cellLocation": {
      "rowIndex": 0,
      "columnIndex": 0
    },
    "text": "Header",
    "insertionIndex": 0
  }
}
```

### Image Operations

**createImage** - Insert an image from URL:
```json
{
  "createImage": {
    "objectId": "image_1",
    "url": "https://example.com/image.png",
    "elementProperties": {
      "pageObjectId": "slide_1",
      "size": {
        "width": {"magnitude": 3000000, "unit": "EMU"},
        "height": {"magnitude": 3000000, "unit": "EMU"}
      },
      "transform": {
        "scaleX": 1, "scaleY": 1,
        "translateX": 3000000, "translateY": 1000000,
        "unit": "EMU"
      }
    }
  }
}
```

### Line Operations

**createLine** - Create a line or connector:
```json
{
  "createLine": {
    "objectId": "line_1",
    "lineCategory": "STRAIGHT",
    "elementProperties": {
      "pageObjectId": "slide_1",
      "size": {
        "width": {"magnitude": 2000000, "unit": "EMU"},
        "height": {"magnitude": 0, "unit": "EMU"}
      },
      "transform": {
        "scaleX": 1, "scaleY": 1,
        "translateX": 1000000, "translateY": 2500000,
        "unit": "EMU"
      }
    }
  }
}
```

### General Operations

**deleteObject** - Delete any element:
```json
{
  "deleteObject": {
    "objectId": "element_to_delete"
  }
}
```

**duplicateObject** - Duplicate an element:
```json
{
  "duplicateObject": {
    "objectId": "element_to_copy"
  }
}
```

**updateSlideProperties** - Change slide background:
```json
{
  "updateSlideProperties": {
    "objectId": "slide_1",
    "slideProperties": {
      "pageBackgroundFill": {
        "solidFill": {
          "color": {
            "rgbColor": {"red": 0.1, "green": 0.1, "blue": 0.2}
          }
        }
      }
    },
    "fields": "pageBackgroundFill.solidFill.color"
  }
}
```

## IMPORTANT RULES

1. **EMU Units**: 1 inch = 914400 EMU. Standard slide is 9144000 x 5143500 EMU (10" x 5.63").

2. **First Slide**: When you create a presentation, it automatically has one blank slide.
   Use `get_presentation_state` to find its objectId and placeholder IDs before inserting text.
   Do NOT create a slide at index 0 — use the existing first slide's placeholders.

3. **Object IDs**: Every objectId must be unique within the presentation.
   Use descriptive IDs like "slide_1", "title_shape_2", "body_text_3".

4. **Execution Order**: Requests are executed in array order. Create objects before
   referencing them (e.g., createSlide before insertText on that slide).

5. **Error Recovery**: If execute_slide_requests returns errors:
   - Read the error messages carefully
   - Common issues: wrong objectId, object doesn't exist yet, invalid layout
   - Call get_presentation_state to see the CURRENT state
   - Generate corrected requests and retry

6. **Editing**: When the user asks to change something:
   - ALWAYS call get_presentation_state first to see what exists
   - Use the actual objectIds from the state, not guessed ones
   - To change text: deleteText (ALL) then insertText
   - To change style: updateTextStyle or updateShapeProperties
   - To change layout: you may need to delete and recreate elements

7. **Colors**: Use RGB values from 0.0 to 1.0. Common colors:
   - Red: {red: 1.0, green: 0.0, blue: 0.0}
   - Blue: {red: 0.0, green: 0.0, blue: 1.0}
   - Green: {red: 0.0, green: 0.5, blue: 0.0}
   - White: {red: 1.0, green: 1.0, blue: 1.0}
   - Black: {red: 0.0, green: 0.0, blue: 0.0}
   - Dark blue: {red: 0.1, green: 0.2, blue: 0.5}

8. **Fields Parameter**: For update operations, the `fields` parameter specifies which
   properties to update. Use dot notation for nested fields. Only listed fields are changed.

9. **Presentation Flow**: For a typical presentation:
   - Slide 1: Title slide (TITLE layout)
   - Slides 2-N: Content slides (TITLE_AND_BODY, TITLE_AND_TWO_COLUMNS, etc.)
   - Last slide: Summary/Thank You (TITLE or SECTION_HEADER)

10. **Be Conversational**: When speaking with the user:
    - Confirm what you're creating before doing it
    - Report progress ("Creating your presentation...", "Adding slides...")
    - Share the URL when done
    - Ask if they want to make any changes
"""

# ============================================================================
# AGENT DEFINITION
# ============================================================================

# Voice agent — uses native audio model for bidi-streaming (voice input/output)
agent = Agent(
    model="gemini-2.5-flash-native-audio-preview-12-2025",
    name="slidemakr",
    description="AI agent that creates and edits Google Slides from natural language",
    instruction=AGENT_INSTRUCTION,
    tools=[
        create_new_presentation,
        execute_slide_requests,
        get_presentation_state,
        share_presentation_with_user,
        search_company_branding,
    ],
)

# Text agent — uses standard model for reliable tool calls via POST /generate
text_agent = Agent(
    model="gemini-2.5-flash",
    name="slidemakr_text",
    description="AI agent that creates and edits Google Slides from text instructions",
    instruction=AGENT_INSTRUCTION,
    tools=[
        create_new_presentation,
        execute_slide_requests,
        get_presentation_state,
        share_presentation_with_user,
        search_company_branding,
    ],
)

# ============================================================================
# EDIT AGENT (for voice editing of existing presentations)
# ============================================================================

EDIT_INSTRUCTION = """You are SlideMakr in editing mode. You modify existing presentations via voice commands.

The presentation state has been loaded — you know every slide, element, objectId, and text content.

## HOW TO EDIT

1. The user speaks a command like "change the title to Hello World" or "make the background blue"
2. Identify which element(s) to modify using objectIds from the presentation state
3. Generate the correct Google Slides API request(s)
4. Call `execute_slide_requests` with the request(s)
5. Briefly confirm: "Done, changed the title to Hello World"

## COMMON EDIT PATTERNS

- **Change text**: `deleteText` (type: ALL) then `insertText` on the same objectId
- **Change text style**: `updateTextStyle` with fields like fontSize, bold, foregroundColor, fontFamily
- **Change background**: `updateSlideProperties` with pageBackgroundFill
- **Change shape fill**: `updateShapeProperties` with shapeBackgroundFill
- **Add element**: `createShape` or `createImage` on a specific slide
- **Remove element**: `deleteObject` with the objectId
- **Add slide**: `createSlide` with appropriate layout

## RULES

1. Use ACTUAL objectIds from the presentation state — never guess
2. If the user says "this slide" or "the title", infer from context or ask
3. Be brief — just confirm what you changed, don't repeat the full context
4. EMU: 1 inch = 914400 EMU. Slide = 9144000 x 5143500 EMU
5. Colors: RGB 0.0-1.0 scale
6. If a command is ambiguous, ask a short clarifying question
7. After editing, if you need to see the updated state, call `get_presentation_state`

## GOOGLE SLIDES API QUICK REFERENCE

- insertText: {objectId, text, insertionIndex: 0}
- deleteText: {objectId, textRange: {type: "ALL"}}
- updateTextStyle: {objectId, style: {...}, textRange: {type: "ALL"}, fields: "..."}
- updateSlideProperties: {objectId, slideProperties: {pageBackgroundFill: {solidFill: {color: {rgbColor: {...}}}}}, fields: "..."}
- updateShapeProperties: {objectId, shapeProperties: {...}, fields: "..."}
- createShape: {objectId, shapeType, elementProperties: {pageObjectId, size, transform}}
- deleteObject: {objectId}
- createSlide: {objectId, insertionIndex, slideLayoutReference: {predefinedLayout: "..."}}
"""

# Edit agent — uses native audio model for real-time voice editing via bidi
edit_agent = Agent(
    model="gemini-2.5-flash-native-audio-preview-12-2025",
    name="slidemakr_editor",
    description="AI agent that edits existing Google Slides presentations via voice commands",
    instruction=EDIT_INSTRUCTION,
    tools=[
        execute_slide_requests,
        get_presentation_state,
        share_presentation_with_user,
    ],
)
