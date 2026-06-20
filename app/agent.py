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
from . import slide_batch
from .slides_schema import validate_typed_requests
from .narrow_tools import (
    # Slide-level
    add_slide,
    reorder_slides,
    update_slide_flags,
    set_slide_background,
    # Element creation
    add_shape,
    add_text_box,
    add_image,
    add_table,
    add_line,
    # Text
    insert_text,
    delete_text,
    update_text,
    replace_all_text,
    update_text_style,
    set_paragraph_style,
    add_bullets,
    # Transforms / styling
    move_element,
    resize_element,
    delete_element,
    duplicate_element,
    set_element_color,
    # Tables
    insert_table_row,
    insert_table_column,
    delete_table_row,
    delete_table_column,
    set_cell_background,
    merge_cells,
    unmerge_cells,
    # Lines
    set_line_style,
    # Commit (Mode A only; no-op in Mode B)
    commit_edits,
)

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


def execute_slide_requests(
    presentation_id: str,
    requests: List[Dict[str, Any]],
) -> dict:
    """Execute a BATCH of Google Slides API requests with validation, retry, and verification.

    The `requests` parameter is a TYPED array — each element must be exactly one
    of the allowed Google Slides batchUpdate request shapes (createSlide,
    createShape, insertText, updateTextStyle, updateShapeProperties, …).
    The schema enforces that hallucinated request types (moveElement, resize,
    setColor, etc.) cannot be produced; you must use the real API shapes.

    Always send ALL edits as a SINGLE batch in one call — this is the fastest
    path and matches how the Slides API is designed.

    Args:
        presentation_id: The Google Slides presentation ID
        requests: Array of typed request objects. Each object has exactly one key
                  that is one of the allowed request type names, with its value
                  matching that type's schema.

    Returns:
        dict with execution results including success count, errors, URL,
        and a verification summary of the current presentation state
    """
    # === Defence in depth — reject anything that slipped past Gemini's schema ===
    try:
        requests = validate_typed_requests(requests)
    except ValueError as e:
        return {
            'status': 'error',
            'error': str(e),
            'hint': (
                "Each element of `requests` must be a dict with exactly one "
                "key matching an allowed Slides API request type."
            ),
        }

    # === PASS 1: Execute all requests ===
    result = slidemakr.execute_slide_requests(presentation_id, requests)

    all_errors = []
    if 'errors' in result:
        all_errors.extend(result['errors'])

    # === PASS 2: Retry failed requests once ===
    if result.get('error_count', 0) > 0 and 'errors' in result:
        failed_requests = [e['request'] for e in result['errors'] if 'request' in e]
        if failed_requests:
            logging.info(f"Retrying {len(failed_requests)} failed requests for {presentation_id}")
            retry_result = slidemakr.execute_slide_requests(presentation_id, failed_requests)

            # Update counts: add retry successes
            retry_successes = retry_result.get('success_count', 0)
            if retry_successes > 0:
                result['success_count'] = result.get('success_count', 0) + retry_successes
                result['error_count'] = result.get('error_count', 0) - retry_successes
                logging.info(f"Retry recovered {retry_successes} requests")

            # Replace errors with only still-failing ones
            if retry_result.get('errors'):
                all_errors = retry_result['errors']
            else:
                all_errors = []

    # === PASS 3: Verify by reading presentation state ===
    verification = {}
    try:
        state = slidemakr.get_presentation_state(presentation_id)
        slide_count = state.get('slide_count', 0)
        slides_summary = []
        for s in state.get('slides', [])[:5]:  # first 5 slides
            slide_text = []
            for elem in s.get('elements', []):
                if elem.get('text'):
                    slide_text.append(elem['text'][:100])
            slides_summary.append({
                'slide_id': s.get('slide_id', ''),
                'element_count': len(s.get('elements', [])),
                'text_preview': ' | '.join(slide_text)[:200]
            })
        verification = {
            'title': state.get('title', ''),
            'slide_count': slide_count,
            'slides_after_edit': slides_summary,
        }
    except Exception as e:
        verification = {'error': f'Could not verify: {str(e)}'}

    # Log all errors to database
    for error in all_errors:
        db.record_error(
            presentation_id=presentation_id,
            request_json=json.dumps(error.get('request', {})),
            error_message=error.get('error', 'unknown')
        )

    # Update presentation status
    db.update_presentation_status(
        presentation_id=presentation_id,
        status=result.get('status', 'unknown'),
        request_count=result.get('total', 0)
    )

    # Build final response with verification
    success_count = result.get('success_count', 0)
    total = result.get('total', 0)
    error_count = len(all_errors)

    final = {
        'url': result.get('url', f'https://docs.google.com/presentation/d/{presentation_id}/edit'),
        'presentation_id': presentation_id,
        'verification': verification,
    }

    if error_count == 0:
        final['status'] = 'success'
        final['summary'] = f'All {total} request(s) executed successfully.'
        final['success_count'] = success_count
        final['total'] = total
    elif success_count > 0:
        final['status'] = 'partial_failure'
        final['success_count'] = success_count
        final['total'] = total
        final['error_count'] = error_count
        final['failed_requests'] = [
            {'request_type': list(e.get('request', {}).keys())[0] if e.get('request') else 'unknown',
             'error': e.get('error', 'unknown')}
            for e in all_errors
        ]
        final['summary'] = (
            f'WARNING: Only {success_count}/{total} requests succeeded. '
            f'{error_count} FAILED. You MUST tell the user what failed and fix it. '
            f'Do NOT say "done" — the edit is INCOMPLETE.'
        )
    else:
        final['status'] = 'all_failed'
        final['success_count'] = 0
        final['total'] = total
        final['error_count'] = error_count
        final['failed_requests'] = [
            {'request_type': list(e.get('request', {}).keys())[0] if e.get('request') else 'unknown',
             'error': e.get('error', 'unknown')}
            for e in all_errors
        ]
        final['summary'] = (
            f'CRITICAL: ALL {total} requests FAILED. NOTHING was changed. '
            f'Read the errors below, fix the requests, and call execute_slide_requests again. '
            f'Do NOT tell the user the edit was made — it was NOT.'
        )

    return final


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


def search_web(query: str) -> dict:
    """Search the web for information using Google Search via Gemini.

    Use this when the user asks you to look up real data, statistics, facts,
    or any information from the web to include in their presentation.
    For example: company revenue, market stats, recent news, product specs.

    Args:
        query: The search query (e.g. "Ergatta revenue 2025", "AI market size forecast")

    Returns:
        dict with search results text
    """
    try:
        client = genai.Client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"Search the web and provide factual, up-to-date information about: {query}\n\nReturn the key facts, numbers, and data points in a concise format.",
            config=genai_types.GenerateContentConfig(
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
            ),
        )
        return {
            'status': 'success',
            'query': query,
            'results': response.text,
        }
    except Exception as e:
        logging.error(f"search_web failed: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'hint': 'Web search failed. Ask the user to provide the data directly.'
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

1. Call `create_new_presentation` with a compelling title (`use_template=True` for styled slides).
2. Call `get_presentation_state` to find the first slide's placeholder objectIds.
3. Fill the first slide with `insert_text` and `update_text_style`.
4. For each additional slide: call `add_slide(layout=..., title_id=..., body_id=...)`,
   then `insert_text` on the returned title_id / body_id, then `add_bullets` / `update_text_style`.
5. For images: call `search_web_image(query)` first, then `add_image(slide_id, url, x, y, w, h)`.
6. For charts: call `create_chart(...)` first, then `add_image(slide_id, chart_url, x, y, w, h)`.
7. For flowcharts: call `create_flowchart(slide_id, nodes_json, edges_json)`.
8. End every editing turn by calling `commit_edits(presentation_id)` — this flushes all your
   queued narrow-tool edits to Google in ONE batchUpdate. (If `commit_edits` reports status=noop
   it's fine — it just means you're in immediate-execute mode.)
9. Tell the user the URL. Do NOT call review_slide_layout during creation — the template
   handles layout.

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

## Slide placeholders

Pass `title_id` and `body_id` to `add_slide` to pre-name the layout's
placeholders — then use those IDs in the same turn with `insert_text`,
`add_bullets`, etc. No need for a second `get_presentation_state` call.

## How to edit slides — narrow tools

Call the narrow tool for the specific edit you want. You CAN (and SHOULD) emit
multiple tool calls in one turn — they're buffered and flushed together by
`commit_edits`. Never invent request types; only the registered tools exist.

Slide-level
- `add_slide(insertion_index, layout, title_id, body_id)` — new slide
- `reorder_slides(slide_ids, insertion_index)` — reorder
- `set_slide_background(slide_id, color_hex)` — solid background color
- `update_slide_flags(slide_id, is_skipped)` — presentation flags

Elements (position in EMU; slide = 9_144_000 × 5_143_500)
- `add_text_box(slide_id, text, x, y, w, h)` — text box + text in one call
- `add_shape(slide_id, shape_type, x, y, w, h)` — RECTANGLE / ELLIPSE / DIAMOND / …
- `add_image(slide_id, url, x, y, w, h)` — image from URL
- `add_table(slide_id, rows, cols, x, y, w, h)` — table
- `add_line(slide_id, x, y, w, h)` — line
- `move_element(object_id, x, y)` — move to absolute position
- `resize_element(object_id, scale_x, scale_y, x, y)` — scale + preserve x,y
- `duplicate_element(object_id)` / `delete_element(object_id)` — clone / remove

Text
- `insert_text(object_id, text, insertion_index, cell_row, cell_col)`
- `update_text(object_id, new_text)` — full replace
- `delete_text(object_id, range_type, start, end)`
- `replace_all_text(find, replace, match_case, slide_ids)`
- `update_text_style(object_id, bold, italic, color_hex, size_pt, font, ...)`
- `set_paragraph_style(object_id, alignment, line_spacing, ...)`
- `add_bullets(object_id, preset)` — BULLET_DISC_CIRCLE_SQUARE / BULLET_STAR_CIRCLE_SQUARE / NUMBERED_DIGIT_ALPHA_ROMAN / …

Shape / line styling
- `set_element_color(object_id, fill_color_hex, outline_color_hex, outline_weight_pt)`
- `set_line_style(object_id, weight_pt, dash_style, color_hex)`

Tables
- `insert_table_row(table_id, row, column, below, count)`
- `insert_table_column(table_id, row, column, right, count)`
- `delete_table_row(table_id, row, column)` / `delete_table_column(table_id, row, column)`
- `set_cell_background(table_id, row_start, col_start, row_span, col_span, color_hex)`
- `merge_cells(...)` / `unmerge_cells(...)`

Flushing
- `commit_edits(presentation_id)` — END every editing turn with this.

Images & charts first-fetch
- `search_web_image(query)` for photos, then `add_image(...)` with the returned URL
- `create_chart(type, labels_json, datasets_json, title)` for data, then `add_image(...)` with the returned `chart_url`
- `create_flowchart(slide_id, nodes_json, edges_json, layout)` draws the whole flowchart itself

Shape types: TEXT_BOX, RECTANGLE, ROUND_RECTANGLE, ELLIPSE, DIAMOND, TRIANGLE, STAR_5, HEXAGON.
Bullet presets: BULLET_DISC_CIRCLE_SQUARE, BULLET_ARROW_DIAMOND_DISC, BULLET_STAR_CIRCLE_SQUARE, NUMBERED_DIGIT_ALPHA_ROMAN.

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

1. Call `get_presentation_state` to see slide + element objectIds and current text.
2. Call the narrow tools above with the ACTUAL objectIds from the state — never guess.
3. End with `commit_edits(presentation_id)` to flush.

## RULES

1. **EMU units**: 1 inch = 914_400 EMU. Slide = 9_144_000 × 5_143_500 EMU.
2. **First slide**: Template gives you one — use its placeholders, don't create a new "slide 0".
3. **Colors**: All narrow tools take `color_hex="#RRGGBB"` strings. Don't pass RGB floats.
4. **Error recovery**: If a tool result has `status` != `success`/`queued`, call
   `get_presentation_state` and try again with correct objectIds.
5. **Trust the template**: With `use_template=True`, don't reposition/resize placeholders.
6. **Speed**: Do not call `review_slide_layout` during creation.
7. **Commit**: Always call `commit_edits(presentation_id)` at the end of an editing turn.
"""

# ============================================================================
# AGENT DEFINITION
# ============================================================================

# NOTE on tool schema (Step 15 — tool decomposition):
# The old single `execute_slide_requests(requests: List[Dict])` tool has been
# replaced by 28 narrow typed tools (one per Slides API request) plus a
# `commit_edits` flush tool. Each narrow tool has a small focused schema
# (~200-500 bytes), so the total tool-declaration budget stays well under the
# ~10 KB that native-audio Gemini Live accepts. Hallucinated request types
# like moveElement / setColor can no longer be emitted because they're not
# registered tools. `execute_slide_requests` and `validate_typed_requests`
# remain in the module as escape hatches but are no longer registered on
# either agent.


# Narrow slide-editing tools (Step 15): one per Slides API request + 2 compounds + commit.
NARROW_SLIDE_TOOLS = [
    # Slide-level
    add_slide,
    reorder_slides,
    update_slide_flags,
    set_slide_background,
    # Element creation
    add_shape,
    add_text_box,
    add_image,
    add_table,
    add_line,
    # Text
    insert_text,
    delete_text,
    update_text,
    replace_all_text,
    update_text_style,
    set_paragraph_style,
    add_bullets,
    # Transforms & styling
    move_element,
    resize_element,
    delete_element,
    duplicate_element,
    set_element_color,
    # Tables
    insert_table_row,
    insert_table_column,
    delete_table_row,
    delete_table_column,
    set_cell_background,
    merge_cells,
    unmerge_cells,
    # Lines
    set_line_style,
    # Flush
    commit_edits,
]


# Voice agent — uses native audio model for bidi-streaming (voice input/output)
TOOLS = [
    create_new_presentation,
    get_presentation_state,
    get_template_layouts,
    share_presentation_with_user,
    search_company_branding,
    apply_brand_theme,
    search_web,
    search_web_image,
    create_chart,
    create_flowchart,
    *NARROW_SLIDE_TOOLS,
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

## ABSOLUTE RULE: NEVER LIE ABOUT RESULTS
After calling `commit_edits(presentation_id)` at the end of your turn, READ the response:
- If `error_count > 0` → tell the user what failed, then fix it and retry.
- If status is `success` with a sane `verification` → then you can confirm the edit.
- If you haven't called `commit_edits` yet, nothing has been sent to Google — your
  narrow-tool calls are queued only.
The user can SEE the presentation. If you say "done" but nothing changed, you lose trust.
When in doubt, call `get_presentation_state` to verify.

## Drive Mode (when no presentation is loaded yet)

If no presentation is loaded, you're in Drive mode. The user will tell you what they want:
- "Find my Q4 board review" → call `search_drive_presentations(query="Q4 board review")`
- "Open the Ergatta pitch deck" → search first, then `open_presentation(id)`
- "Duplicate my Ergatta deck for Scale" → search, then `duplicate_presentation(id, "Scale Pitch Deck")`, then `open_presentation(new_id)`
- "Create a new deck based on my investor update" → search, duplicate, then edit

After opening a presentation, ALWAYS tell the user the presentation name and URL so they can see it.
Then proceed to editing mode below.

## Editing Mode — Narrow Tools Workflow

1. **Read the state.** Call `get_presentation_state(presentation_id)`. Use the actual
   objectIds you find — never guess. Note positions (translateX/Y + width/height) so
   you can place new elements without overlapping.

2. **Plan spatially.** Prefer side-by-side layouts for visual + text. Use the
   POSITIONING RECIPES below.

3. **Call narrow tools.** Emit one or more of the registered tools (see list below).
   You can fire multiple tool calls in one turn — they're batched server-side.
   If a tool returns `status: "error"` with `valid_object_ids`, you targeted an
   objectId that doesn't exist — retry with one of the `valid_object_ids` it lists.
   Never invent IDs; only edit/delete elements you saw in `get_presentation_state`.

4. **End every edit turn with `commit_edits(presentation_id)`.** This flushes all your
   queued changes in ONE batchUpdate HTTP call and returns a verification. If you
   forget this, nothing ships to Google.

5. **Read the commit result.** `error_count`, `committed_request_count`, and
   `verification.first_titles` tell you what actually landed. Only confirm success
   after this — don't guess.

## Registered narrow tools (use these and ONLY these — no invented names)

Slide-level
- `add_slide(insertion_index, layout, title_id, body_id)`
- `reorder_slides(slide_ids, insertion_index)`
- `update_slide_flags(slide_id, is_skipped)`
- `set_slide_background(slide_id, color_hex)`

Elements
- `add_text_box(slide_id, text, x, y, w, h)` — box + text, one call
- `add_shape(slide_id, shape_type, x, y, w, h)` — RECTANGLE / ELLIPSE / DIAMOND / …
- `add_image(slide_id, url, x, y, w, h)` — URL from `search_web_image` or `create_chart`
- `add_table(slide_id, rows, cols, x, y, w, h)`
- `add_line(slide_id, x, y, w, h)`
- `move_element(object_id, x, y)` / `resize_element(object_id, scale_x, scale_y, x, y)`
- `duplicate_element(object_id)` / `delete_element(object_id)`

Text
- `insert_text(object_id, text, insertion_index, cell_row, cell_col)`
- `update_text(object_id, new_text)` — full replace
- `delete_text(object_id, range_type, start, end)`
- `replace_all_text(find, replace, match_case, slide_ids)`
- `update_text_style(object_id, bold, italic, color_hex, size_pt, font, ...)`
- `set_paragraph_style(object_id, alignment, line_spacing, ...)`
- `add_bullets(object_id, preset)`

Styling
- `set_element_color(object_id, fill_color_hex, outline_color_hex, outline_weight_pt)`
- `set_line_style(object_id, weight_pt, dash_style, color_hex)`

Tables
- `insert_table_row` / `insert_table_column` / `delete_table_row` / `delete_table_column`
- `set_cell_background(table_id, row_start, col_start, row_span, col_span, color_hex)`
- `merge_cells(...)` / `unmerge_cells(...)`

Flush
- `commit_edits(presentation_id)` — ALWAYS call last

Images & charts: call `search_web_image(query)` or `create_chart(...)` first to get a
URL, then pass the URL to `add_image(...)`.
Flowcharts: `create_flowchart(slide_id, nodes_json, edges_json, layout)` draws the
whole flowchart itself — no narrow tools needed for diagrams.

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

# ============================================================================
# DRIVE TOOLS (for Drive Picker / Edit Existing flow)
# ============================================================================

def search_drive_presentations(query: str = "") -> dict:
    """Search the user's Google Drive for presentations.

    Use this when the user asks to find a presentation by name or description.
    Returns a list of matching presentations with their IDs and names.

    Args:
        query: Search query to filter presentations by name.
               Leave empty to list recent presentations.

    Returns:
        dict with list of presentations (id, name, modifiedTime)
    """
    try:
        results = slidemakr.search_presentations(query=query)
        if not results:
            return {
                'status': 'success',
                'presentations': [],
                'message': f'No presentations found{" matching " + repr(query) if query else ""}.'
            }
        return {
            'status': 'success',
            'presentations': [
                {'id': f['id'], 'name': f['name'], 'modified': f.get('modifiedTime', '')}
                for f in results
            ]
        }
    except Exception as e:
        logging.error(f"search_drive_presentations failed: {e}")
        return {'status': 'error', 'error': str(e)}


def duplicate_presentation(presentation_id: str, new_title: str) -> dict:
    """Duplicate a presentation in the user's Google Drive.

    Creates a copy of the presentation with a new title.
    Use this when the user wants to create a new deck based on an existing one.

    Args:
        presentation_id: The ID of the presentation to copy
        new_title: Title for the new copy

    Returns:
        dict with new presentation_id, name, and url
    """
    try:
        result = slidemakr.duplicate_presentation(presentation_id, new_title)
        return {
            'status': 'success',
            **result
        }
    except Exception as e:
        logging.error(f"duplicate_presentation failed: {e}")
        return {'status': 'error', 'error': str(e)}


def open_presentation(presentation_id: str) -> dict:
    """Open a presentation for editing by loading its full state.

    Call this after finding a presentation via search_drive_presentations
    or after duplicating one. This loads the slide content so you can
    start making edits.

    Args:
        presentation_id: The Google Slides presentation ID to open

    Returns:
        dict with the full presentation state (slides, elements, text, etc.)
    """
    try:
        state = slidemakr.get_presentation_state(presentation_id)
        return {
            'status': 'success',
            'message': f"Opened '{state.get('title', '')}' with {state.get('slide_count', 0)} slides.",
            'presentation_id': presentation_id,
            'url': f'https://docs.google.com/presentation/d/{presentation_id}/edit',
            'state': state
        }
    except Exception as e:
        logging.error(f"open_presentation failed: {e}")
        return {'status': 'error', 'error': str(e)}


# Edit agent — uses native audio model for real-time voice editing via bidi.
# Step 15: no longer registers `execute_slide_requests`; narrow tools replace it.
edit_agent = Agent(
    model="gemini-2.5-flash-native-audio-latest",
    name="slidemakr_editor",
    description="AI agent that edits existing Google Slides presentations via voice commands",
    instruction=EDIT_INSTRUCTION,
    tools=[
        get_presentation_state,
        get_template_layouts,
        share_presentation_with_user,
        search_company_branding,
        apply_brand_theme,
        search_web,
        search_web_image,
        create_chart,
        create_flowchart,
        review_slide_layout,
        # Drive tools (for Edit Existing flow)
        search_drive_presentations,
        duplicate_presentation,
        open_presentation,
        # Narrow slide-editing tools (Step 15)
        *NARROW_SLIDE_TOOLS,
    ],
    generate_content_config=CREATIVE_CONFIG,
)
