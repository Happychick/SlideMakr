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
- apply_brand_theme: Apply complete brand theme (colors, fonts, logo) in one shot

The agent generates valid Google Slides API JSON directly via the instruction
prompt — no separate RAG database or nested LLM call needed.
"""

import json
import logging
import os
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


def get_template_layouts(presentation_id: str) -> dict:
    """Get available slide layouts from the presentation's template.

    Call this after creating a presentation to see which layouts are available.
    Use layout objectIds with createSlide to create properly designed slides
    instead of placing shapes manually.

    Args:
        presentation_id: The Google Slides presentation ID

    Returns:
        dict with list of layouts, each containing objectId, name, and placeholders
    """
    try:
        layouts = slidemakr.get_template_layouts(presentation_id)
        return {
            'status': 'success',
            'layouts': layouts
        }
    except Exception as e:
        logging.error(f"get_template_layouts failed: {e}")
        return {
            'status': 'error',
            'error': str(e)
        }


def create_flowchart(
    presentation_id: str,
    slide_id: str,
    nodes_json: str,
    edges_json: str,
    title: str = "",
    layout: str = "vertical",
) -> dict:
    """Create a flowchart on a specific slide.

    Use this when the user asks for a flowchart, process diagram, decision tree,
    or any kind of flow visualization. You provide the logical structure (nodes
    and edges) and this tool handles all the positioning, shapes, connectors,
    and styling automatically.

    IMPORTANT: You must create the slide first (createSlide), then call this tool.
    Use get_presentation_state to find the slide's objectId.

    Args:
        presentation_id: The Google Slides presentation ID
        slide_id: The objectId of the slide to draw the flowchart on
        nodes_json: JSON array of nodes. Each node has:
            - "id": unique string ID (e.g., "start", "step1", "decision1")
            - "label": display text (e.g., "Start", "Process Data", "Is Valid?")
            - "type": shape type — one of:
                "start"/"end"/"oval" — ellipse (for start/end nodes)
                "process"/"rectangle" — rectangle (for process steps)
                "decision"/"diamond" — diamond (for yes/no decisions)
                "subroutine"/"rounded" — rounded rectangle (for sub-processes)
            Example: '[{"id":"start","label":"Start","type":"oval"},{"id":"step1","label":"Process Data","type":"process"}]'
        edges_json: JSON array of edges connecting nodes. Each edge has:
            - "from": source node ID
            - "to": target node ID
            - "label": optional edge label (e.g., "Yes", "No", "Success")
            Example: '[{"from":"start","to":"step1"},{"from":"decision1","to":"step2","label":"Yes"}]'
        title: Optional title text displayed at the top of the flowchart
        layout: Layout direction. One of:
            - "vertical" — top-to-bottom flow (default, best for simple linear processes)
            - "horizontal" — left-to-right flow (best for timelines, pipelines, wide processes)
            - "tree" — auto-detects best direction based on graph shape

    Returns:
        dict with execution results and node_object_ids for further editing
    """
    try:
        nodes = json.loads(nodes_json)
        edges = json.loads(edges_json)
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"Invalid JSON: {e}"}

    if not nodes:
        return {"status": "error", "error": "No nodes provided"}

    # Validate layout
    if layout not in ("vertical", "horizontal", "tree"):
        layout = "vertical"

    from .flowchart import generate_flowchart_requests

    requests, layout_meta = generate_flowchart_requests(
        slide_id=slide_id,
        nodes=nodes,
        edges=edges,
        title=title or None,
        layout=layout,
    )

    # Check for overflow BEFORE executing
    if not layout_meta["fits_slide"]:
        # Suggest trying the other orientation
        alt = "horizontal" if layout == "vertical" else "vertical"
        return {
            "status": "overflow",
            "layout_used": layout_meta.get("layout", layout),
            "total_nodes": layout_meta["total_nodes"],
            "levels_used": layout_meta["levels_used"],
            "hint": f"Too many nodes ({layout_meta['total_nodes']}) across {layout_meta['levels_used']} levels — "
                    f"exceeds slide bounds. Try layout='{alt}' for a different orientation, "
                    f"or split into 2 slides with max 6-8 nodes each.",
        }

    result = slidemakr.execute_slide_requests(presentation_id, requests)

    # Log errors
    if "errors" in result:
        for error in result["errors"]:
            db.record_error(
                presentation_id=presentation_id,
                request_json=json.dumps(error["request"]),
                error_message=error["error"],
            )

    # Include layout stats and node objectIds in the result
    result["layout"] = {
        "layout_direction": layout_meta.get("layout", layout),
        "levels_used": layout_meta["levels_used"],
        "nodes_per_level": layout_meta["nodes_per_level"],
        "fits_slide": True,
    }
    # Return node objectIds so the agent can further edit individual shapes
    result["node_object_ids"] = layout_meta.get("node_object_ids", {})

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
        # Check cache first
        cached = db.get_cached_brand(company_name)
        if cached:
            return {
                'status': 'success',
                'company': company_name,
                'branding': cached.get('branding_text', ''),
                'cached': True,
            }

        client = genai.Client()

        prompt = f"""Search for {company_name}'s brand guidelines and visual identity.

Return a structured summary with EXACTLY these fields for easy parsing:

**PRIMARY_COLOR_HEX**: #XXXXXX (the main brand color as hex)
**SECONDARY_COLOR_HEX**: #XXXXXX (secondary brand color as hex, or "none")
**ACCENT_COLOR_HEX**: #XXXXXX (accent/highlight color as hex, or same as primary)
**HEADING_FONT**: FontName (the typeface they use for headings, e.g., "Montserrat")
**BODY_FONT**: FontName (the typeface for body text, e.g., "Open Sans")
**LOGO_URL**: direct URL to their logo image (prefer PNG from their press kit or official site)
**DARK_BACKGROUND**: true/false (does the brand typically use dark backgrounds?)
**STYLE_NOTES**: Brief description of their visual style

Be specific and accurate. If you can't find exact brand guidelines, use colors visible on their website.
Always provide hex codes. Example: Stripe → PRIMARY_COLOR_HEX: #635BFF, DARK_BACKGROUND: true"""

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
            ),
        )

        # Cache for future use
        branding_text = response.text
        db.save_brand_cache(company_name, branding_text)

        return {
            'status': 'success',
            'company': company_name,
            'branding': branding_text,
        }
    except Exception as e:
        logging.error(f"search_company_branding failed: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'hint': 'Web search may not be available. Use default styling or ask the user for brand colors.'
        }


def search_web_image(query: str, count: int = 3) -> dict:
    """Search Unsplash for high-quality photos. Returns URLs ready for Google Slides.

    Use this when the user wants images, photos, or illustrations in their slides.
    The returned URLs can be used directly with createImage in execute_slide_requests.

    IMPORTANT: Always call this tool to get real image URLs. Never make up or guess URLs.

    Args:
        query: What to search for (e.g., "solar panels renewable energy", "rocket launch space",
               "artificial intelligence brain"). Be specific and descriptive for better results.
        count: How many image URLs to return (default 3, max 5)

    Returns:
        dict with list of image URLs ready for use with createImage
    """
    import requests as http_req

    try:
        access_key = os.environ.get('UNSPLASH_ACCESS_KEY', '').strip()
        if not access_key:
            return {
                'status': 'error',
                'error': 'UNSPLASH_ACCESS_KEY not set',
                'hint': 'Image search unavailable. Skip images for this slide.',
            }

        resp = http_req.get(
            "https://api.unsplash.com/search/photos",
            params={'query': query, 'per_page': count, 'orientation': 'landscape'},
            headers={'Authorization': f'Client-ID {access_key}'},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get('results', [])

        if not results:
            return {
                'status': 'error',
                'query': query,
                'image_urls': [],
                'hint': 'No images found for this query. Try a different search term or skip images.',
            }

        # Extract the regular-size URLs (1080px wide, perfect for slides)
        image_urls = []
        for photo in results:
            url = photo.get('urls', {}).get('regular')
            if url:
                image_urls.append(url)

        logging.info(f"search_web_image found {len(image_urls)} Unsplash images for '{query}'")

        return {
            'status': 'success',
            'query': query,
            'image_urls': image_urls,
            'count': len(image_urls),
        }
    except Exception as e:
        logging.error(f"search_web_image failed: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'hint': 'Image search failed. Skip images or ask the user for image URLs.',
        }


def create_chart(
    chart_type: str,
    labels_json: str,
    datasets_json: str,
    title: str = "",
    width: int = 800,
    height: int = 500,
) -> dict:
    """Create a chart and return an image URL for use with createImage.

    Generates a professional chart image that can be embedded in a slide using createImage.
    Supports bar charts, line charts, pie charts, doughnut charts, and more.

    Args:
        chart_type: Type of chart. One of: "bar", "line", "pie", "doughnut", "radar",
                    "horizontalBar", "polarArea"
        labels_json: JSON array of labels for the x-axis or segments.
                     Example: '["Q1", "Q2", "Q3", "Q4"]'
                     Example for pie: '["Marketing", "Engineering", "Sales", "Support"]'
        datasets_json: JSON array of dataset objects. Each has "label" and "data".
                       Example single dataset: '[{"label": "Revenue", "data": [100, 200, 150, 300]}]'
                       Example multiple: '[{"label": "Revenue", "data": [100, 200]}, {"label": "Costs", "data": [80, 120]}]'
                       For pie/doughnut, use a single dataset: '[{"label": "Budget", "data": [30, 25, 20, 25]}]'
        title: Optional chart title displayed at the top
        width: Image width in pixels (default 800)
        height: Image height in pixels (default 500)

    Returns:
        dict with chart_url ready for createImage, plus the recommended EMU size
    """
    import urllib.parse

    try:
        labels = json.loads(labels_json)
        datasets = json.loads(datasets_json)
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"Invalid JSON: {e}"}

    if not labels or not datasets:
        return {"status": "error", "error": "Labels and datasets are required"}

    # Professional color palette
    colors = [
        "rgba(54, 162, 235, 0.8)",   # Blue
        "rgba(255, 99, 132, 0.8)",   # Red/Pink
        "rgba(75, 192, 192, 0.8)",   # Teal
        "rgba(255, 206, 86, 0.8)",   # Yellow
        "rgba(153, 102, 255, 0.8)",  # Purple
        "rgba(255, 159, 64, 0.8)",   # Orange
        "rgba(46, 204, 113, 0.8)",   # Green
        "rgba(142, 68, 173, 0.8)",   # Dark Purple
    ]
    border_colors = [c.replace("0.8", "1") for c in colors]

    # Build Chart.js datasets with colors
    chart_datasets = []
    for i, ds in enumerate(datasets):
        chart_ds = {
            "label": ds.get("label", f"Series {i+1}"),
            "data": ds.get("data", []),
        }
        if chart_type in ("pie", "doughnut", "polarArea"):
            # Pie/doughnut: colors per segment
            chart_ds["backgroundColor"] = colors[:len(labels)]
            chart_ds["borderColor"] = border_colors[:len(labels)]
        else:
            # Bar/line: color per dataset
            chart_ds["backgroundColor"] = colors[i % len(colors)]
            chart_ds["borderColor"] = border_colors[i % len(colors)]
            if chart_type == "line":
                chart_ds["fill"] = False
                chart_ds["borderWidth"] = 3
        chart_datasets.append(chart_ds)

    # Build Chart.js config
    chart_config = {
        "type": chart_type,
        "data": {
            "labels": labels,
            "datasets": chart_datasets,
        },
        "options": {
            "plugins": {
                "legend": {"display": len(datasets) > 1 or chart_type in ("pie", "doughnut")},
            },
            "scales": {} if chart_type in ("pie", "doughnut", "polarArea", "radar") else {
                "y": {"beginAtZero": True},
            },
        },
    }

    if title:
        chart_config["options"]["plugins"]["title"] = {
            "display": True,
            "text": title,
            "font": {"size": 18},
        }

    # Build QuickChart URL
    chart_json = json.dumps(chart_config, separators=(',', ':'))
    chart_url = f"https://quickchart.io/chart?c={urllib.parse.quote(chart_json)}&w={width}&h={height}&bkg=white&f=png"

    logging.info(f"create_chart: {chart_type} with {len(labels)} labels, {len(datasets)} datasets")

    return {
        "status": "success",
        "chart_url": chart_url,
        "chart_type": chart_type,
        "recommended_width_emu": 6000000,   # ~6.5 inches
        "recommended_height_emu": 3750000,  # ~4.1 inches (maintains 800:500 ratio)
        "hint": "Use this chart_url with createImage in your execute_slide_requests batch.",
    }


def review_slide_layout(presentation_id: str, slide_id: str) -> dict:
    """Visually review a slide's layout by looking at a rendered thumbnail.

    Call this after making edits to verify the slide looks professional.
    This tool renders the actual slide as an image and uses AI vision to
    check for layout issues like overlapping elements, poor spacing,
    awkward text placement, or missing visual hierarchy.

    Args:
        presentation_id: The Google Slides presentation ID
        slide_id: The objectId of the slide to review

    Returns:
        dict with 'assessment' (text feedback) and 'issues' (list of problems found)
    """
    try:
        png_bytes = slidemakr.get_slide_thumbnail(presentation_id, slide_id, "LARGE")
        if not png_bytes:
            return {
                "status": "error",
                "error": "Could not fetch slide thumbnail",
                "hint": "Use get_presentation_state to review element positions instead.",
            }

        client = genai.Client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_bytes(
                            data=png_bytes,
                            mime_type="image/png",
                        ),
                        genai_types.Part.from_text(
                            text="""You are a presentation design reviewer. Analyze this slide and identify layout issues.

Check for:
1. OVERLAPPING elements — text covering images, shapes on top of each other
2. AWKWARD PLACEMENT — text floating with no visual anchor, elements not aligned
3. POOR HIERARCHY — title not prominent enough, all text same size/weight
4. CRAMPED CONTENT — too much text, insufficient whitespace
5. VISUAL BALANCE — is content weighted to one side with empty space elsewhere?
6. READABILITY — small fonts, low contrast text, text extending beyond visible area

For each issue found, specify:
- What the problem is
- WHERE on the slide (top-left, center, bottom-right, etc.)
- How to fix it (resize, move, delete, restyle)

If the slide looks GOOD, say so! Not every slide has issues.

Respond as JSON:
{
  "overall_quality": "good" | "needs_fixes" | "poor",
  "issues": [
    {"problem": "...", "location": "...", "fix": "..."}
  ],
  "summary": "One sentence assessment"
}"""
                        ),
                    ],
                )
            ],
        )

        assessment_text = response.text.strip()

        # Try to parse as JSON
        try:
            # Strip markdown code fences if present
            clean = assessment_text
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
            if clean.startswith("json"):
                clean = clean[4:].strip()
            assessment = json.loads(clean)
        except (json.JSONDecodeError, IndexError):
            assessment = {"summary": assessment_text, "issues": [], "overall_quality": "unknown"}

        return {
            "status": "success",
            "assessment": assessment,
        }

    except Exception as e:
        logging.error(f"review_slide_layout failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "hint": "Visual review unavailable. Use get_presentation_state to check layout.",
        }


def apply_brand_theme(
    presentation_id: str,
    primary_color_hex: str,
    secondary_color_hex: str = "",
    accent_color_hex: str = "",
    heading_font: str = "",
    body_font: str = "",
    logo_url: str = "",
    dark_background: bool = False,
) -> dict:
    """Apply a complete brand theme to an existing presentation.

    Call this AFTER creating slides to apply consistent branding in one shot.
    This tool auto-applies: slide backgrounds, text colors, heading fonts, body fonts,
    and optionally inserts a logo on the title slide.

    The agent should call search_company_branding first, then pass the extracted
    brand details to this tool.

    Args:
        presentation_id: The Google Slides presentation ID to theme
        primary_color_hex: Primary brand color as hex (e.g., "#635BFF" for Stripe)
        secondary_color_hex: Secondary brand color as hex (optional, defaults to white/dark)
        accent_color_hex: Accent color for highlights (optional)
        heading_font: Font family for headings (e.g., "Montserrat"). Empty = keep current.
        body_font: Font family for body text (e.g., "Open Sans"). Empty = keep current.
        logo_url: Direct URL to logo image. Will be inserted on the title slide.
        dark_background: Set True for dark slide backgrounds with light text.

    Returns:
        dict with status, changes_applied count, and any warnings
    """
    import re

    def hex_to_rgb(hex_str: str) -> dict:
        """Convert hex color to Google Slides RGB (0.0-1.0 scale)."""
        hex_str = hex_str.strip().lstrip('#')
        if len(hex_str) != 6:
            return {'red': 0.0, 'green': 0.0, 'blue': 0.0}
        r = int(hex_str[0:2], 16) / 255.0
        g = int(hex_str[2:4], 16) / 255.0
        b = int(hex_str[4:6], 16) / 255.0
        return {'red': round(r, 4), 'green': round(g, 4), 'blue': round(b, 4)}

    try:
        # Get current presentation state
        state = slidemakr.get_presentation_state(presentation_id)
        if not state or 'slides' not in state:
            return {'status': 'error', 'error': 'Could not read presentation state'}

        slides = state['slides']
        requests = []
        changes = 0
        warnings = []

        # Parse colors
        primary_rgb = hex_to_rgb(primary_color_hex)

        if dark_background:
            bg_rgb = primary_rgb
            title_text_rgb = {'red': 1.0, 'green': 1.0, 'blue': 1.0}
            body_text_rgb = {'red': 0.9, 'green': 0.9, 'blue': 0.9}
        else:
            # Light background: use white/light bg, primary for titles
            bg_rgb = {'red': 1.0, 'green': 1.0, 'blue': 1.0}
            title_text_rgb = primary_rgb
            body_text_rgb = {'red': 0.15, 'green': 0.15, 'blue': 0.15}

        accent_rgb = hex_to_rgb(accent_color_hex) if accent_color_hex else primary_rgb

        # 1. Apply backgrounds to all slides
        for slide in slides:
            slide_id = slide.get('objectId')
            if not slide_id:
                continue

            requests.append({
                'updatePageProperties': {
                    'objectId': slide_id,
                    'pageProperties': {
                        'pageBackgroundFill': {
                            'solidFill': {
                                'color': {'rgbColor': bg_rgb}
                            }
                        }
                    },
                    'fields': 'pageBackgroundFill.solidFill.color'
                }
            })
            changes += 1

        # 2. Restyle text elements (titles get primary color, body gets dark/light)
        for slide in slides:
            for elem in slide.get('elements', []):
                obj_id = elem.get('objectId', '')
                placeholder = elem.get('placeholder', '')
                elem_type = elem.get('type', '')
                text = elem.get('text', '')

                if not obj_id:
                    continue

                # Determine if this is a title or body element
                is_title = placeholder in ('TITLE', 'CENTERED_TITLE', 'SUBTITLE')
                is_body = placeholder in ('BODY', 'SUBTITLE') or (
                    elem_type == 'shape' and text and len(text) > 20
                )

                # Apply text color
                if is_title or is_body:
                    text_rgb = title_text_rgb if is_title else body_text_rgb
                    style_update = {
                        'updateTextStyle': {
                            'objectId': obj_id,
                            'style': {
                                'foregroundColor': {
                                    'opaqueColor': {'rgbColor': text_rgb}
                                }
                            },
                            'textRange': {'type': 'ALL'},
                            'fields': 'foregroundColor'
                        }
                    }
                    requests.append(style_update)
                    changes += 1

                    # Apply fonts if specified
                    if is_title and heading_font:
                        requests.append({
                            'updateTextStyle': {
                                'objectId': obj_id,
                                'style': {'fontFamily': heading_font},
                                'textRange': {'type': 'ALL'},
                                'fields': 'fontFamily'
                            }
                        })
                        changes += 1
                    elif is_body and body_font:
                        requests.append({
                            'updateTextStyle': {
                                'objectId': obj_id,
                                'style': {'fontFamily': body_font},
                                'textRange': {'type': 'ALL'},
                                'fields': 'fontFamily'
                            }
                        })
                        changes += 1

        # 3. Style shapes (non-text shapes) with accent color outline
        for slide in slides:
            for elem in slide.get('elements', []):
                shape_type = elem.get('shapeType', '')
                if shape_type and shape_type not in ('TEXT_BOX', None, ''):
                    obj_id = elem.get('objectId', '')
                    if obj_id:
                        requests.append({
                            'updateShapeProperties': {
                                'objectId': obj_id,
                                'shapeProperties': {
                                    'outline': {
                                        'outlineFill': {
                                            'solidFill': {
                                                'color': {'rgbColor': accent_rgb}
                                            }
                                        },
                                        'weight': {'magnitude': 2, 'unit': 'PT'}
                                    }
                                },
                                'fields': 'outline'
                            }
                        })
                        changes += 1

        # 4. Insert logo on title slide (first slide)
        logo_inserted = False
        if logo_url:
            try:
                # Upload logo to Drive first for reliable access
                drive_url = slidemakr.upload_image_to_drive(logo_url, "brand_logo.png")
                if drive_url:
                    first_slide_id = slides[0].get('objectId') if slides else None
                    if first_slide_id:
                        # Place logo in bottom-right corner of title slide
                        requests.append({
                            'createImage': {
                                'objectId': f'brand_logo_{presentation_id[:8]}',
                                'url': drive_url,
                                'elementProperties': {
                                    'pageObjectId': first_slide_id,
                                    'size': {
                                        'width': {'magnitude': 1200000, 'unit': 'EMU'},
                                        'height': {'magnitude': 600000, 'unit': 'EMU'},
                                    },
                                    'transform': {
                                        'scaleX': 1, 'scaleY': 1,
                                        'translateX': 7600000,  # Right side
                                        'translateY': 4200000,  # Bottom area
                                        'unit': 'EMU'
                                    }
                                }
                            }
                        })
                        changes += 1
                        logo_inserted = True
                else:
                    warnings.append(f"Could not upload logo from {logo_url[:60]}")
            except Exception as e:
                warnings.append(f"Logo insertion failed: {str(e)[:100]}")

        # 5. Execute all branding requests
        if requests:
            result = slidemakr.execute_slide_requests(presentation_id, requests)
            if result.get('error_count', 0) > 0:
                warnings.append(f"{result['error_count']} requests had errors")

        return {
            'status': 'success',
            'changes_applied': changes,
            'slides_themed': len(slides),
            'logo_inserted': logo_inserted,
            'colors': {
                'primary': primary_color_hex,
                'secondary': secondary_color_hex or 'default',
                'dark_mode': dark_background,
            },
            'fonts': {
                'heading': heading_font or 'unchanged',
                'body': body_font or 'unchanged',
            },
            'warnings': warnings,
            'hint': 'Brand theme applied! The agent can now focus on content.',
        }

    except Exception as e:
        logging.error(f"apply_brand_theme failed: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'hint': 'Theme application failed. Apply colors manually via execute_slide_requests.',
        }


# ============================================================================
# AGENT INSTRUCTION PROMPT
# ============================================================================

AGENT_INSTRUCTION = """You are SlideMakr, a creative AI assistant that creates beautiful Google Slides
presentations from natural language. You bring energy and visual flair to every presentation.

## WORKFLOW — Creating a New Presentation

Follow these steps IN ORDER:

### Step 1: Create the presentation
Call `create_new_presentation` with a compelling title (set `use_template=True` for styled slides).

### Step 2: Read the first slide
Call `get_presentation_state` to find the existing first slide's objectId and placeholder IDs.
The template gives you a first slide with TITLE and SUBTITLE placeholders — use their objectIds.

### Step 3: Build ALL slides in ONE batch
Generate a SINGLE JSON array with ALL requests and call `execute_slide_requests` ONCE.

**For the first slide** — use the existing placeholder objectIds from step 2:
```json
[
  {"insertText": {"objectId": "i0", "text": "Your Title", "insertionIndex": 0}},
  {"insertText": {"objectId": "i1", "text": "Your Subtitle", "insertionIndex": 0}}
]
```

**For every new slide** — use `createSlide` with `placeholderIdMappings` to pre-assign objectIds:
```json
{
  "createSlide": {
    "objectId": "slide_1",
    "insertionIndex": 1,
    "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
    "placeholderIdMappings": [
      {"layoutPlaceholder": {"type": "TITLE"}, "objectId": "title_1"},
      {"layoutPlaceholder": {"type": "BODY"}, "objectId": "body_1"}
    ]
  }
}
```
Then IMMEDIATELY reference those objectIds (title_1, body_1) in insertText requests
that follow in the SAME array — no need to call get_presentation_state in between.

### Step 4: Tell the user the URL
Do NOT call review_slide_layout during creation — the template handles layout. Just create and go.

## CHOOSING THE RIGHT LAYOUT

Pick the best layout for each slide's purpose:

| Layout | Use For | Placeholders |
|--------|---------|-------------|
| TITLE | Title/opening slides, closing slides | CENTERED_TITLE, SUBTITLE |
| SECTION_HEADER | Section transitions between topics | TITLE, BODY |
| TITLE_AND_BODY | Most content slides — title + bullets/text | TITLE, BODY |
| TITLE_AND_TWO_COLUMNS | Comparisons, pros/cons, two-column content | TITLE, BODY (x2) |
| TITLE_ONLY | Slides needing custom content below a title | TITLE |
| MAIN_POINT | Key takeaways, big statements | TITLE, BODY |
| BIG_NUMBER | Statistics, metrics, key numbers | TITLE, BODY |
| BLANK | Flowcharts, custom layouts, images only | (none) |

## placeholderIdMappings — THE KEY PATTERN

This is critical! When you create a slide with a layout, use `placeholderIdMappings` to assign
your own objectIds to the layout's placeholders. Then you can insert text RIGHT AWAY:

```json
[
  {
    "createSlide": {
      "objectId": "slide_2",
      "insertionIndex": 2,
      "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
      "placeholderIdMappings": [
        {"layoutPlaceholder": {"type": "TITLE"}, "objectId": "title_2"},
        {"layoutPlaceholder": {"type": "BODY"}, "objectId": "body_2"}
      ]
    }
  },
  {"insertText": {"objectId": "title_2", "text": "Market Analysis", "insertionIndex": 0}},
  {"insertText": {"objectId": "body_2", "text": "Revenue grew 45% YoY\\nCustomer base expanded to 2M+\\nMarket share increased from 12% to 18%", "insertionIndex": 0}},
  {"createParagraphBullets": {"objectId": "body_2", "textRange": {"type": "ALL"}, "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}}
]
```

## BULLET POINTS

To create bullet points, insert text with newlines (\\n) between items, then add bullets:
```json
[
  {"insertText": {"objectId": "body_1", "text": "First point\\nSecond point\\nThird point", "insertionIndex": 0}},
  {"createParagraphBullets": {"objectId": "body_1", "textRange": {"type": "ALL"}, "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}}
]
```
Bullet presets: BULLET_DISC_CIRCLE_SQUARE, BULLET_ARROW_DIAMOND_DISC,
BULLET_STAR_CIRCLE_SQUARE, NUMBERED_DIGIT_ALPHA_ROMAN

## COMPLETE EXAMPLE — 3-Slide Presentation

After creating the presentation and reading the first slide (objectIds i0, i1):

```json
[
  {"insertText": {"objectId": "i0", "text": "Q4 Business Review", "insertionIndex": 0}},
  {"insertText": {"objectId": "i1", "text": "Building momentum for 2026", "insertionIndex": 0}},

  {"createSlide": {"objectId": "slide_1", "insertionIndex": 1,
    "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
    "placeholderIdMappings": [
      {"layoutPlaceholder": {"type": "TITLE"}, "objectId": "title_1"},
      {"layoutPlaceholder": {"type": "BODY"}, "objectId": "body_1"}
    ]}},
  {"insertText": {"objectId": "title_1", "text": "Key Highlights", "insertionIndex": 0}},
  {"insertText": {"objectId": "body_1", "text": "Revenue: $12.5M (+45% YoY)\\nNew customers: 850\\nNPS score: 72 (up from 65)\\nChurn reduced to 3.2%", "insertionIndex": 0}},
  {"createParagraphBullets": {"objectId": "body_1", "textRange": {"type": "ALL"}, "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}},

  {"createSlide": {"objectId": "slide_2", "insertionIndex": 2,
    "slideLayoutReference": {"predefinedLayout": "SECTION_HEADER"},
    "placeholderIdMappings": [
      {"layoutPlaceholder": {"type": "TITLE"}, "objectId": "title_2"},
      {"layoutPlaceholder": {"type": "BODY"}, "objectId": "body_2"}
    ]}},
  {"insertText": {"objectId": "title_2", "text": "Next Steps", "insertionIndex": 0}},
  {"insertText": {"objectId": "body_2", "text": "Expand into European markets by Q2", "insertionIndex": 0}}
]
```

## STYLING

**updateTextStyle** — change font, size, color, bold:
```json
{"updateTextStyle": {"objectId": "title_1", "style": {"bold": true, "fontSize": {"magnitude": 28, "unit": "PT"}, "fontFamily": "Arial"}, "textRange": {"type": "ALL"}, "fields": "bold,fontSize,fontFamily"}}
```

**updatePageProperties** — change slide background (NOT updateSlideProperties):
```json
{"updatePageProperties": {"objectId": "slide_1", "pageProperties": {"pageBackgroundFill": {"solidFill": {"color": {"rgbColor": {"red": 0.1, "green": 0.1, "blue": 0.2}}}}}, "fields": "pageBackgroundFill.solidFill.color"}}
```

**updateShapeProperties** — change shape fill:
```json
{"updateShapeProperties": {"objectId": "shape_1", "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": {"red": 0.2, "green": 0.5, "blue": 0.9}}}}}, "fields": "shapeBackgroundFill.solidFill.color"}}
```

## TABLES

```json
[
  {"createTable": {"objectId": "table_1", "elementProperties": {"pageObjectId": "slide_1", "size": {"width": {"magnitude": 7000000, "unit": "EMU"}, "height": {"magnitude": 3000000, "unit": "EMU"}}, "transform": {"scaleX": 1, "scaleY": 1, "translateX": 1000000, "translateY": 1500000, "unit": "EMU"}}, "rows": 3, "columns": 3}},
  {"insertText": {"objectId": "table_1", "cellLocation": {"rowIndex": 0, "columnIndex": 0}, "text": "Header", "insertionIndex": 0}}
]
```

## IMAGES — Use search_web_image!

When the user wants images, photos, or illustrations in their slides:
1. Call `search_web_image` with a descriptive query (e.g., "AI healthcare technology photo")
2. Use the returned URLs with createImage in your execute_slide_requests batch
3. NEVER make up or guess URLs — always use search_web_image first

```json
{"createImage": {"objectId": "img_1", "url": "USE_URL_FROM_search_web_image", "elementProperties": {"pageObjectId": "slide_1", "size": {"width": {"magnitude": 4000000, "unit": "EMU"}, "height": {"magnitude": 3000000, "unit": "EMU"}}, "transform": {"scaleX": 1, "scaleY": 1, "translateX": 4800000, "translateY": 1200000, "unit": "EMU"}}}}
```

Image placement tips:
- Right side of a text slide: translateX=4800000, width=4000000
- Full-width banner: translateX=0, width=9144000, height=2500000
- Small icon/logo: width=1500000, height=1500000
- Always leave room for text — don't overlap placeholders

## CHARTS — Use create_chart!

When the user wants charts, graphs, or data visualizations:
1. Call `create_chart` with type, labels, and datasets
2. Use the returned `chart_url` with createImage in your execute_slide_requests batch

Chart types: "bar", "line", "pie", "doughnut", "horizontalBar", "radar", "polarArea"

**Example — bar chart:**
```
create_chart(
  chart_type="bar",
  labels_json='["Q1", "Q2", "Q3", "Q4"]',
  datasets_json='[{"label": "Revenue ($M)", "data": [12, 19, 15, 25]}]',
  title="Quarterly Revenue"
)
```

**Example — pie chart:**
```
create_chart(
  chart_type="pie",
  labels_json='["Marketing", "Engineering", "Sales", "Support"]',
  datasets_json='[{"label": "Budget", "data": [30, 40, 20, 10]}]',
  title="Budget Allocation"
)
```

**Example — multi-series line chart:**
```
create_chart(
  chart_type="line",
  labels_json='["Jan", "Feb", "Mar", "Apr", "May"]',
  datasets_json='[{"label": "Users", "data": [1000, 1500, 1800, 2200, 3000]}, {"label": "Revenue", "data": [500, 800, 900, 1200, 1800]}]',
  title="Growth Metrics"
)
```

Then embed the chart with createImage:
```json
{"createImage": {"objectId": "chart_1", "url": "CHART_URL_FROM_create_chart", "elementProperties": {"pageObjectId": "slide_1", "size": {"width": {"magnitude": 6000000, "unit": "EMU"}, "height": {"magnitude": 3750000, "unit": "EMU"}}, "transform": {"scaleX": 1, "scaleY": 1, "translateX": 1500000, "translateY": 1200000, "unit": "EMU"}}}}
```

## SHAPES

Use createShape for custom visual elements, callout boxes, icons, or diagram parts:
```json
{"createShape": {"objectId": "box_1", "shapeType": "ROUND_RECTANGLE", "elementProperties": {"pageObjectId": "slide_1", "size": {"width": {"magnitude": 3000000, "unit": "EMU"}, "height": {"magnitude": 500000, "unit": "EMU"}}, "transform": {"scaleX": 1, "scaleY": 1, "translateX": 500000, "translateY": 500000, "unit": "EMU"}}}}
```
shapeTypes: TEXT_BOX, RECTANGLE, ROUND_RECTANGLE, ELLIPSE, DIAMOND, TRIANGLE, STAR_5, HEXAGON

## FLOWCHARTS & DIAGRAMS — Use create_flowchart!

When the user asks for a flowchart, process diagram, decision tree, or any flow visualization:
1. Create a BLANK slide (createSlide with predefinedLayout BLANK), OR use an existing slide's objectId
2. Call `create_flowchart` with the slide_id, nodes, edges, and layout direction
3. The tool handles ALL positioning, shapes, connectors, and styling automatically
4. The tool returns `node_object_ids` — a map of node ID → shape objectId for further editing

**Layout options** — choose based on the diagram type:
- `layout="vertical"` — top-to-bottom (default). Best for simple linear processes.
- `layout="horizontal"` — left-to-right. Best for timelines, pipelines, wide workflows.
- `layout="tree"` — auto-detects best direction based on graph shape.

**If create_flowchart returns "overflow"**: Try `layout="horizontal"` (or vice versa),
or split into 2 slides with max 6-8 nodes each.

**Example — vertical flowchart:**
```
create_flowchart(
  presentation_id="...", slide_id="flow_slide", layout="vertical",
  nodes_json='[{"id":"start","label":"Start","type":"oval"},{"id":"step1","label":"Process Data","type":"process"},{"id":"check","label":"Valid?","type":"decision"},{"id":"end","label":"Done","type":"oval"}]',
  edges_json='[{"from":"start","to":"step1"},{"from":"step1","to":"check"},{"from":"check","to":"end","label":"Yes"}]',
  title="Data Pipeline"
)
```

**Example — horizontal pipeline:**
```
create_flowchart(
  presentation_id="...", slide_id="pipe_slide", layout="horizontal",
  nodes_json='[{"id":"input","label":"Raw Data","type":"oval"},{"id":"clean","label":"Clean & Transform","type":"process"},{"id":"model","label":"ML Model","type":"process"},{"id":"deploy","label":"Deploy","type":"oval"}]',
  edges_json='[{"from":"input","to":"clean"},{"from":"clean","to":"model"},{"from":"model","to":"deploy"}]',
  title="ML Pipeline"
)
```

**Editing nodes after creation**: The result includes `node_object_ids` (e.g., `{"start": "node_abc123"}`).
Use these objectIds with updateShapeProperties, updateTextStyle, or deleteText+insertText
in execute_slide_requests to restyle or rewrite individual nodes.

Node types: "oval"/"start"/"end" (ellipse), "process"/"rectangle", "decision"/"diamond", "subroutine"/"rounded"
Edge labels are optional: use for Yes/No on decisions.

## BRANDED PRESENTATIONS

If the user mentions a company name, follow this workflow:

1. Call `search_company_branding` to get brand colors, fonts, and logo URL
2. Create the presentation and all slides (content first)
3. Call `apply_brand_theme` with the extracted hex colors, fonts, and logo URL

This applies backgrounds, text colors, fonts, and logo in one shot — much faster
than manually styling each element. Extract from the branding response:
- primary_color_hex: main brand color (e.g., "#635BFF")
- secondary_color_hex: secondary color if available
- heading_font / body_font: brand typefaces
- logo_url: direct URL to their logo
- dark_background: True if the brand uses dark backgrounds (e.g., Stripe, Figma)

## EDITING EXISTING PRESENTATIONS

1. Call `get_presentation_state` to see all slides, elements, objectIds, and text
2. Use the ACTUAL objectIds from the state (never guess)
3. To change text: deleteText (type: ALL) then insertText
4. To change style: updateTextStyle or updateShapeProperties
5. Call `execute_slide_requests` with the edit requests

## RULES

1. **EMU Units**: 1 inch = 914400 EMU. Slide = 9144000 x 5143500 EMU (10" x 5.63").
2. **First Slide**: Template gives you a first slide — use its placeholders, don't create slide 0.
3. **Unique IDs**: Every objectId must be unique (slide_1, title_1, body_1, etc.).
4. **Order**: Create objects before referencing them (createSlide before insertText).
5. **Colors**: RGB 0.0–1.0. White={1,1,1}, Black={0,0,0}, Dark blue={0.1,0.2,0.5}.
6. **Backgrounds**: Use `updatePageProperties` (NOT updateSlideProperties).
7. **Error Recovery**: If errors occur, call `get_presentation_state` and retry with corrected requests.
8. **Be Creative**: Make presentations visually engaging — use varied layouts, clear structure,
   and professional design. Think like a presentation designer, not just a text generator.
9. **Trust the Template**: When using a template (use_template=True), the layout and styling
   is already handled. Just fill in content using the placeholder objectIds. Do NOT try to
   reposition, resize, or restyle template elements — they are already well-designed.
10. **Speed**: Create slides as fast as possible. Do NOT call review_slide_layout during creation.
"""

# ============================================================================
# AGENT DEFINITION
# ============================================================================

# Voice agent — uses native audio model for bidi-streaming (voice input/output)
TOOLS = [
    create_new_presentation,
    execute_slide_requests,
    get_presentation_state,
    get_template_layouts,
    share_presentation_with_user,
    search_company_branding,
    apply_brand_theme,
    search_web_image,
    create_chart,
    create_flowchart,
]

# Creative temperature — gives the agent more freedom for compelling content
CREATIVE_CONFIG = genai_types.GenerateContentConfig(temperature=0.6)

# Voice agent — uses native audio model for bidi-streaming (voice input/output)
agent = Agent(
    model="gemini-2.5-flash-native-audio-latest",
    name="slidemakr",
    description="AI agent that creates and edits Google Slides from natural language",
    instruction=AGENT_INSTRUCTION,
    tools=TOOLS,
    generate_content_config=CREATIVE_CONFIG,
)

# Text agent — uses standard model for reliable tool calls via POST /generate
text_agent = Agent(
    model="gemini-2.5-flash",
    name="slidemakr_text",
    description="AI agent that creates and edits Google Slides from text instructions",
    instruction=AGENT_INSTRUCTION,
    tools=TOOLS,
    generate_content_config=CREATIVE_CONFIG,
)

# ============================================================================
# EDIT AGENT (for voice editing of existing presentations)
# ============================================================================

EDIT_INSTRUCTION = """You are SlideMakr's voice editor. You modify existing presentations via spoken commands.
You are a presentation DESIGNER — every edit should make the slide look MORE professional, not less.

When the user speaks, follow this workflow:

### Step 1: Read the slide state
Call `get_presentation_state` to see all slides, elements, objectIds, text, AND POSITIONS.
Study the layout: where is each element? What's the bounding box? Where is there free space?

### Step 2: Plan the edit spatially
Before generating requests, think about WHERE new content will go:
- What elements already exist on this slide and where are they positioned?
- Where is the FREE SPACE on the slide?
- If adding a visual + text, plan a SIDE-BY-SIDE layout (visual left, text right)
- If the slide is already full, consider: resize existing elements, use a new slide, or replace content

### Step 3: Execute the edit
Call `execute_slide_requests` with well-positioned requests.

### Step 4: Verify (for complex edits)
For edits that ADD new elements (charts, images, text boxes, flowcharts):
Call `review_slide_layout` with the slide_id to VISUALLY check the result.
This tool renders the slide as an image and identifies overlaps, awkward placement,
and readability issues. If it reports issues, fix them before confirming.
For simple edits (text change, color change), skip this step.

### Step 5: Confirm briefly
"Done — added a bar chart with bullet points on slide 2."

## Common Edits

**Change text**: deleteText (type: ALL) then insertText
```json
[
  {"deleteText": {"objectId": "ACTUAL_ID", "textRange": {"type": "ALL"}}},
  {"insertText": {"objectId": "ACTUAL_ID", "text": "New text here", "insertionIndex": 0}}
]
```

**Add bullets**: insertText with newlines, then createParagraphBullets
```json
[
  {"deleteText": {"objectId": "ACTUAL_ID", "textRange": {"type": "ALL"}}},
  {"insertText": {"objectId": "ACTUAL_ID", "text": "Point one\\nPoint two\\nPoint three", "insertionIndex": 0}},
  {"createParagraphBullets": {"objectId": "ACTUAL_ID", "textRange": {"type": "ALL"}, "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}}
]
```

**Add a new slide**: use placeholderIdMappings to pre-assign IDs
```json
[
  {"createSlide": {"objectId": "new_slide", "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
    "placeholderIdMappings": [
      {"layoutPlaceholder": {"type": "TITLE"}, "objectId": "new_title"},
      {"layoutPlaceholder": {"type": "BODY"}, "objectId": "new_body"}
    ]}},
  {"insertText": {"objectId": "new_title", "text": "Slide Title", "insertionIndex": 0}},
  {"insertText": {"objectId": "new_body", "text": "Content here", "insertionIndex": 0}}
]
```

**Style text**: updateTextStyle (fontSize, bold, foregroundColor, fontFamily)
**Background**: updatePageProperties with pageBackgroundFill (NOT updateSlideProperties)
**Shape fill**: updateShapeProperties with shapeBackgroundFill

**Add image**: Call `search_web_image` first to get a real URL, then use createImage:
```json
{"createImage": {"objectId": "img_1", "url": "URL_FROM_SEARCH", "elementProperties": {"pageObjectId": "slide_id", "size": {"width": {"magnitude": 3000000, "unit": "EMU"}, "height": {"magnitude": 2500000, "unit": "EMU"}}, "transform": {"scaleX": 1, "scaleY": 1, "translateX": 5500000, "translateY": 1500000, "unit": "EMU"}}}}
```

**Add a text box** (or any shape): Use createShape, then insertText to add content:
```json
[
  {"createShape": {"objectId": "textbox_1", "shapeType": "TEXT_BOX", "elementProperties": {"pageObjectId": "SLIDE_ID", "size": {"width": {"magnitude": 4000000, "unit": "EMU"}, "height": {"magnitude": 800000, "unit": "EMU"}}, "transform": {"scaleX": 1, "scaleY": 1, "translateX": 2500000, "translateY": 2000000, "unit": "EMU"}}}},
  {"insertText": {"objectId": "textbox_1", "text": "Your text here", "insertionIndex": 0}},
  {"updateTextStyle": {"objectId": "textbox_1", "style": {"fontSize": {"magnitude": 18, "unit": "PT"}, "fontFamily": "Arial"}, "textRange": {"type": "ALL"}, "fields": "fontSize,fontFamily"}}
]
```
shapeTypes: TEXT_BOX, RECTANGLE, ROUND_RECTANGLE, ELLIPSE, DIAMOND, TRIANGLE, HEXAGON

**Style a shape** — change fill color, outline:
```json
{"updateShapeProperties": {"objectId": "textbox_1", "shapeProperties": {"shapeBackgroundFill": {"solidFill": {"color": {"rgbColor": {"red": 0.95, "green": 0.95, "blue": 1.0}}}}}, "fields": "shapeBackgroundFill.solidFill.color"}}
```

**Add a table**:
```json
[
  {"createTable": {"objectId": "table_1", "elementProperties": {"pageObjectId": "SLIDE_ID", "size": {"width": {"magnitude": 7000000, "unit": "EMU"}, "height": {"magnitude": 3000000, "unit": "EMU"}}, "transform": {"scaleX": 1, "scaleY": 1, "translateX": 1000000, "translateY": 1500000, "unit": "EMU"}}, "rows": 3, "columns": 3}},
  {"insertText": {"objectId": "table_1", "cellLocation": {"rowIndex": 0, "columnIndex": 0}, "text": "Header 1", "insertionIndex": 0}}
]
```

**Add flowchart**: Call `create_flowchart` with a slide_id. You can use an EXISTING slide or create a new BLANK one.
If placing on an existing slide, pass that slide's objectId. Layout: "vertical", "horizontal", or "tree" (auto-detect).
If it returns "overflow", try a different layout or split into 2 slides with fewer nodes.
The result includes `node_object_ids` so you can edit individual nodes afterward.

**Move / resize an element**: Use updatePageElementTransform:
```json
{"updatePageElementTransform": {"objectId": "ELEMENT_ID", "applyMode": "ABSOLUTE", "transform": {"scaleX": 1, "scaleY": 1, "translateX": 500000, "translateY": 500000, "unit": "EMU"}}}
```

**Delete an element**:
```json
{"deleteObject": {"objectId": "ELEMENT_ID"}}
```

## Rules
- ALWAYS call get_presentation_state first — use ACTUAL objectIds, never guess
- ALWAYS call search_web_image to get real image URLs — never make up URLs
- EMU: 1 inch = 914400. Slide = 9144000 x 5143500 EMU
- Colors: RGB 0.0–1.0
- Be brief and conversational. Confirm what you changed in one sentence.
- If the command is ambiguous, ask a short clarifying question.
- You can create ANY Google Slides API request — createShape, createTable, createImage, updateTextStyle, etc.
  Think of yourself as a bridge between the user's voice and the Google Slides API.

## POSITIONING RECIPES (EMU coordinates)

Slide dimensions: 9144000 x 5143500 EMU (10" x 5.63"). Title area: top ~900000 EMU.

**Full-width content** (text, table below a title):
  translateX=457200, translateY=1000000, width=8229600, height=3800000

**Visual LEFT + Text RIGHT** (chart/image + bullets — PREFERRED for mixed content):
  Visual: translateX=300000, translateY=1000000, width=5000000, height=3500000
  Text:   translateX=5600000, translateY=1000000, width=3200000, height=3500000

**Text LEFT + Visual RIGHT** (bullets + image):
  Text:   translateX=300000, translateY=1000000, width=3200000, height=3500000
  Visual: translateX=3800000, translateY=1000000, width=5000000, height=3500000

**Adding content to a slide that already has a BODY placeholder with text:**
  Don't create a new floating text box — INSERT into the existing body placeholder instead.
  Use the body element's objectId with insertText/deleteText.

**Adding bullets NEXT TO a flowchart or diagram:**
  Create a TEXT_BOX positioned beside the diagram, NOT below it.
  If diagram is centered, resize it to the left half and put bullets on the right.

## CRITICAL ANTI-PATTERNS (never do these)

- NEVER place a text box floating below a shape/chart with no visual connection
- NEVER leave new elements at default (0,0) position — always specify coordinates
- NEVER create tiny text boxes (< 2000000 EMU width) — text will be cramped
- NEVER overlap elements — check positions from get_presentation_state first
- NEVER add content that extends beyond slide bounds (x > 9144000 or y > 5143500)
- When user says "add bullets" to a slide, use the EXISTING body placeholder if one exists —
  don't create a new floating text box

## LAYOUT QUALITY CHECKS

After complex edits (adding 2+ elements), call get_presentation_state and verify:
1. No elements overlap (compare translateX/Y + width/height of all elements)
2. All content is within slide bounds
3. Text and visuals are arranged side-by-side, not stacked awkwardly
4. Titles are large (28-36pt), body text readable (16-18pt)
5. Key metrics are bold and/or colored
"""

# Edit agent — uses native audio model for real-time voice editing via bidi
edit_agent = Agent(
    model="gemini-2.5-flash-native-audio-latest",
    name="slidemakr_editor",
    description="AI agent that edits existing Google Slides presentations via voice commands",
    instruction=EDIT_INSTRUCTION,
    tools=[
        execute_slide_requests,
        get_presentation_state,
        get_template_layouts,
        share_presentation_with_user,
        search_company_branding,
        apply_brand_theme,
        search_web_image,
        create_chart,
        create_flowchart,
        review_slide_layout,
    ],
    generate_content_config=CREATIVE_CONFIG,
)
