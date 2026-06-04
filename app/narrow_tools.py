"""
SlideMakr - Narrow typed tools for Google Slides editing.

Step 15 of PROJECT_PLAN.md: replace the single `execute_slide_requests`
tool with ~28 narrow tools, one per Slides API operation. Each tool has a
small focused schema (~100-500 bytes) so the union of all tool
declarations stays well under the ~10 KB budget that native-audio Gemini
Live can handle.

Each narrow tool:
  1. Accepts primitive-typed arguments (str / int / float / bool).
  2. Builds the matching Pydantic wrapper from `slides_schema`.
  3. Either (Mode A = commit) appends the dumped dict to a session-scoped
     buffer, OR (Mode B = immediate) sends a one-request batchUpdate.
  4. Returns a dict with status, object_id (if applicable), request, and
     in immediate mode the slides_api_result.

Both modes reuse `slides_schema.validate_request()` as defence-in-depth so
the request shape is auto-fixed before it leaves this process.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from . import slide_batch
from . import slidemakr
from . import slides_schema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _gen_id(prefix: str = "obj") -> str:
    """Generate a short, unique object ID for auto-created elements."""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# Slide bounds (EMU). 10" × 5.625" per Google Slides default.
SLIDE_W_EMU = 9_144_000
SLIDE_H_EMU = 5_143_500

# Sensible defaults when Gemini omits a position/size parameter.
# Centered-ish, roughly half the slide.
DEFAULT_X = 1_000_000
DEFAULT_Y = 1_000_000
DEFAULT_W = 6_000_000
DEFAULT_H = 3_500_000


def _emu_size(w: int, h: int) -> Dict[str, Any]:
    return {
        "width": {"magnitude": int(w) if w > 0 else DEFAULT_W, "unit": "EMU"},
        "height": {"magnitude": int(h) if h > 0 else DEFAULT_H, "unit": "EMU"},
    }


def _emu_transform(x: int, y: int, scale_x: float = 1.0, scale_y: float = 1.0) -> Dict[str, Any]:
    return {
        "scaleX": scale_x,
        "scaleY": scale_y,
        "translateX": int(x) if x >= 0 else DEFAULT_X,
        "translateY": int(y) if y >= 0 else DEFAULT_Y,
        "unit": "EMU",
    }


def _element_props(slide_id: str, x: int, y: int, w: int, h: int) -> Dict[str, Any]:
    return {
        "pageObjectId": slide_id,
        "size": _emu_size(w, h),
        "transform": _emu_transform(x, y),
    }


def _submit(
    presentation_id: str,
    request: Dict[str, Any],
    object_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Either buffer or execute a single request based on BATCH_MODE.

    Returns a uniform dict regardless of mode so the LLM sees a stable shape.
    """
    # Defence-in-depth auto-fix
    fixed = slides_schema.validate_request(request)
    if fixed is None:
        return {
            "status": "error",
            "error": "Request dropped by schema validator (invalid type)",
            "request": request,
        }

    if slide_batch.BATCH_MODE == "immediate":
        try:
            result = slidemakr.execute_slide_requests(presentation_id, [fixed])
        except Exception as e:
            logger.error(f"immediate batch failed: {e}")
            return {
                "status": "error",
                "error": str(e),
                "request": fixed,
                "object_id": object_id or "",
            }
        return {
            "status": "success" if result.get("error_count", 0) == 0 else "partial_failure",
            "object_id": object_id or "",
            "request": fixed,
            "slides_api_result": result,
        }

    # Mode A — commit buffer
    pending = slide_batch.append(presentation_id, fixed)
    return {
        "status": "queued",
        "object_id": object_id or "",
        "request": fixed,
        "pending_count": pending,
        "hint": "Queued. Call commit_edits() to flush all pending edits in one batchUpdate.",
    }


# ---------------------------------------------------------------------------
# SLIDE-LEVEL TOOLS
# ---------------------------------------------------------------------------


def add_slide(
    presentation_id: str,
    insertion_index: int = -1,
    layout: str = "TITLE_AND_BODY",
    title_id: str = "",
    body_id: str = "",
    object_id: str = "",
) -> dict:
    """Add a new slide to the presentation.

    Args:
        presentation_id: The presentation to edit.
        insertion_index: 0-based position in the deck. -1 (default) appends at the end.
        layout: Predefined layout — TITLE, SECTION_HEADER, TITLE_AND_BODY,
                TITLE_AND_TWO_COLUMNS, TITLE_ONLY, MAIN_POINT, BIG_NUMBER, BLANK.
        title_id: Optional objectId to assign to the TITLE placeholder — use this in
                  follow-up insert_text calls to write the title.
        body_id: Optional objectId to assign to the BODY placeholder.
        object_id: Optional objectId for the slide itself.

    Returns:
        dict with the slide's objectId and (if set) placeholder objectIds.
    """
    slide_oid = object_id or _gen_id("slide")
    body: Dict[str, Any] = {
        "objectId": slide_oid,
        "slideLayoutReference": {"predefinedLayout": layout},
    }
    if insertion_index >= 0:
        body["insertionIndex"] = insertion_index

    mappings: List[Dict[str, Any]] = []
    if title_id:
        mappings.append({"layoutPlaceholder": {"type": "TITLE"}, "objectId": title_id})
    if body_id:
        mappings.append({"layoutPlaceholder": {"type": "BODY"}, "objectId": body_id})
    if mappings:
        body["placeholderIdMappings"] = mappings

    result = _submit(presentation_id, {"createSlide": body}, object_id=slide_oid)
    result["slide_id"] = slide_oid
    if title_id:
        result["title_id"] = title_id
    if body_id:
        result["body_id"] = body_id
    return result


def reorder_slides(
    presentation_id: str,
    slide_ids: List[str],
    insertion_index: int,
) -> dict:
    """Move one or more slides to a new position in the deck.

    Args:
        presentation_id: The presentation to edit.
        slide_ids: The objectIds of slides to move, in the order they should land.
        insertion_index: 0-based index to insert the moved slides at.

    Returns:
        dict with status and request.
    """
    body = {"slideObjectIds": list(slide_ids), "insertionIndex": int(insertion_index)}
    return _submit(presentation_id, {"updateSlidesPosition": body})


def update_slide_flags(
    presentation_id: str,
    slide_id: str,
    is_skipped: bool = False,
) -> dict:
    """Toggle slide-level flags (currently: `isSkipped` for presenter mode).

    Args:
        presentation_id: The presentation to edit.
        slide_id: The objectId of the slide.
        is_skipped: True to skip this slide during presentation.

    Returns:
        dict with status.
    """
    body = {
        "objectId": slide_id,
        "slideProperties": {"isSkipped": bool(is_skipped)},
        "fields": "isSkipped",
    }
    return _submit(presentation_id, {"updateSlideProperties": body})


def set_slide_background(
    presentation_id: str,
    slide_id: str,
    color_hex: str,
) -> dict:
    """Set a slide's background to a solid color.

    Args:
        presentation_id: The presentation to edit.
        slide_id: The objectId of the slide.
        color_hex: Hex color '#RRGGBB' (e.g. '#0F172A').

    Returns:
        dict with status.
    """
    body = {
        "objectId": slide_id,
        "pageProperties": {
            "pageBackgroundFill": slides_schema.solid_fill_from_hex(color_hex),
        },
        "fields": "pageBackgroundFill.solidFill.color",
    }
    return _submit(presentation_id, {"updatePageProperties": body})


# ---------------------------------------------------------------------------
# ELEMENT CREATION
# ---------------------------------------------------------------------------


def add_shape(
    presentation_id: str,
    slide_id: str,
    shape_type: str,
    x: int = DEFAULT_X,
    y: int = DEFAULT_Y,
    w: int = DEFAULT_W,
    h: int = DEFAULT_H,
    object_id: str = "",
) -> dict:
    """Add a shape (RECTANGLE, ELLIPSE, DIAMOND, TRIANGLE, STAR_5, HEXAGON, etc.).

    Args:
        presentation_id: The presentation to edit.
        slide_id: The objectId of the target slide.
        shape_type: Shape type name (RECTANGLE, ROUND_RECTANGLE, ELLIPSE, DIAMOND,
                    TRIANGLE, STAR_5, HEXAGON, TEXT_BOX, ...).
        x, y: Top-left position in EMU. Defaults centered-ish on slide.
        w, h: Width and height in EMU. Defaults ~6M × 3.5M EMU.
        object_id: Optional objectId to assign.

    Returns:
        dict with status and object_id.
    """
    oid = object_id or _gen_id("shape")
    body = {
        "objectId": oid,
        "shapeType": shape_type,
        "elementProperties": _element_props(slide_id, x, y, w, h),
    }
    result = _submit(presentation_id, {"createShape": body}, object_id=oid)
    result["shape_id"] = oid
    return result


def add_text_box(
    presentation_id: str,
    slide_id: str,
    text: str,
    x: int = DEFAULT_X,
    y: int = DEFAULT_Y,
    w: int = 4_000_000,
    h: int = 800_000,
    object_id: str = "",
) -> dict:
    """Add a TEXT_BOX shape with text in one call.

    Compound convenience: buffers a createShape (TEXT_BOX) AND an insertText so
    the LLM doesn't have to make two calls for the common "add a labeled text
    box" intent.

    Args:
        presentation_id: The presentation to edit.
        slide_id: The objectId of the target slide.
        text: Initial text content.
        x, y: Top-left position in EMU. Defaults centered-ish on slide.
        w, h: Width and height in EMU. Defaults 4M × 800K EMU.
        object_id: Optional objectId to assign.

    Returns:
        dict with status and object_id.
    """
    oid = object_id or _gen_id("tb")
    create = {
        "objectId": oid,
        "shapeType": "TEXT_BOX",
        "elementProperties": _element_props(slide_id, x, y, w, h),
    }
    r1 = _submit(presentation_id, {"createShape": create}, object_id=oid)
    r2 = _submit(
        presentation_id,
        {"insertText": {"objectId": oid, "text": text, "insertionIndex": 0}},
        object_id=oid,
    )
    return {
        "status": r1.get("status", r2.get("status", "success")),
        "object_id": oid,
        "text_box_id": oid,
        "requests": [r1.get("request"), r2.get("request")],
        "pending_count": r2.get("pending_count"),
    }


def add_image(
    presentation_id: str,
    slide_id: str,
    url: str,
    x: int = 1_500_000,
    y: int = 1_200_000,
    w: int = 6_000_000,
    h: int = 3_750_000,
    object_id: str = "",
) -> dict:
    """Add an image at a specific position and size.

    Args:
        presentation_id: The presentation to edit.
        slide_id: The objectId of the target slide.
        url: Publicly accessible image URL (e.g. from search_web_image or create_chart).
        x, y: Top-left position in EMU. Defaults to a centered chart-sized slot.
        w, h: Width and height in EMU. Defaults ~6M × 3.75M EMU (chart aspect).
        object_id: Optional objectId to assign.

    Returns:
        dict with status and object_id.
    """
    oid = object_id or _gen_id("img")
    body = {
        "objectId": oid,
        "url": url,
        "elementProperties": _element_props(slide_id, x, y, w, h),
    }
    result = _submit(presentation_id, {"createImage": body}, object_id=oid)
    result["image_id"] = oid
    return result


def add_table(
    presentation_id: str,
    slide_id: str,
    rows: int,
    cols: int,
    x: int = 1_000_000,
    y: int = 1_500_000,
    w: int = 7_000_000,
    h: int = 3_000_000,
    object_id: str = "",
) -> dict:
    """Add a table with the given dimensions.

    Args:
        presentation_id: The presentation to edit.
        slide_id: The objectId of the target slide.
        rows: Number of rows.
        cols: Number of columns.
        x, y: Top-left position in EMU.
        w, h: Width and height in EMU.
        object_id: Optional objectId to assign.

    Returns:
        dict with status and table_id.
    """
    oid = object_id or _gen_id("tbl")
    body = {
        "objectId": oid,
        "rows": int(rows),
        "columns": int(cols),
        "elementProperties": _element_props(slide_id, x, y, w, h),
    }
    result = _submit(presentation_id, {"createTable": body}, object_id=oid)
    result["table_id"] = oid
    return result


def add_line(
    presentation_id: str,
    slide_id: str,
    x: int = 1_000_000,
    y: int = 2_500_000,
    w: int = 5_000_000,
    h: int = 100_000,
    object_id: str = "",
    line_category: str = "STRAIGHT",
) -> dict:
    """Add a line (straight or bent).

    Args:
        presentation_id: The presentation to edit.
        slide_id: The objectId of the target slide.
        x, y: Top-left of the line's bounding box in EMU.
        w, h: Line bounding-box width and height in EMU. The line is drawn
              along the diagonal.
        object_id: Optional objectId to assign.
        line_category: STRAIGHT, BENT, or CURVED.

    Returns:
        dict with status and line_id.
    """
    oid = object_id or _gen_id("ln")
    body = {
        "objectId": oid,
        "lineCategory": line_category,
        "elementProperties": _element_props(slide_id, x, y, w, h),
    }
    result = _submit(presentation_id, {"createLine": body}, object_id=oid)
    result["line_id"] = oid
    return result


# ---------------------------------------------------------------------------
# TEXT OPERATIONS
# ---------------------------------------------------------------------------


def insert_text(
    presentation_id: str,
    object_id: str,
    text: str,
    insertion_index: int = 0,
    cell_row: int = -1,
    cell_col: int = -1,
) -> dict:
    """Insert text into a shape, text box, or table cell.

    Args:
        presentation_id: The presentation to edit.
        object_id: The objectId of the shape/text box/table.
        text: Text to insert.
        insertion_index: Character index to insert at (0 = beginning).
        cell_row, cell_col: For tables, the 0-based cell coordinates. -1 = not a cell.

    Returns:
        dict with status.
    """
    body: Dict[str, Any] = {
        "objectId": object_id,
        "text": text,
        "insertionIndex": int(insertion_index),
    }
    if cell_row >= 0 and cell_col >= 0:
        body["cellLocation"] = {"rowIndex": int(cell_row), "columnIndex": int(cell_col)}
    return _submit(presentation_id, {"insertText": body}, object_id=object_id)


def delete_text(
    presentation_id: str,
    object_id: str,
    range_type: str = "ALL",
    start: int = 0,
    end: int = 0,
) -> dict:
    """Delete text from an element.

    Args:
        presentation_id: The presentation to edit.
        object_id: The objectId of the element.
        range_type: ALL (default), FIXED_RANGE, or FROM_START_INDEX.
        start: Character start index (used for FIXED_RANGE / FROM_START_INDEX).
        end: Character end index (used for FIXED_RANGE).

    Returns:
        dict with status.
    """
    text_range: Dict[str, Any] = {"type": range_type}
    if range_type != "ALL":
        text_range["startIndex"] = int(start)
    if range_type == "FIXED_RANGE":
        text_range["endIndex"] = int(end)
    body = {"objectId": object_id, "textRange": text_range}
    return _submit(presentation_id, {"deleteText": body}, object_id=object_id)


def update_text(
    presentation_id: str,
    object_id: str,
    new_text: str,
) -> dict:
    """Replace all text in an element with new text.

    Compound convenience: buffers a deleteText(ALL) followed by an insertText.

    Args:
        presentation_id: The presentation to edit.
        object_id: The objectId of the element.
        new_text: New text content.

    Returns:
        dict with status.
    """
    r1 = _submit(
        presentation_id,
        {"deleteText": {"objectId": object_id, "textRange": {"type": "ALL"}}},
        object_id=object_id,
    )
    r2 = _submit(
        presentation_id,
        {"insertText": {"objectId": object_id, "text": new_text, "insertionIndex": 0}},
        object_id=object_id,
    )
    return {
        "status": r2.get("status", r1.get("status", "success")),
        "object_id": object_id,
        "requests": [r1.get("request"), r2.get("request")],
        "pending_count": r2.get("pending_count"),
    }


def replace_all_text(
    presentation_id: str,
    find: str,
    replace: str,
    match_case: bool = False,
    slide_ids: str = "",
) -> dict:
    """Find and replace text across the deck (or a subset of slides).

    Args:
        presentation_id: The presentation to edit.
        find: Substring to find.
        replace: Replacement text.
        match_case: Whether the search is case-sensitive.
        slide_ids: Comma-separated slide objectIds to restrict the search to.
                   Empty string = all slides.

    Returns:
        dict with status.
    """
    body: Dict[str, Any] = {
        "containsText": {"text": find, "matchCase": bool(match_case)},
        "replaceText": replace,
    }
    if slide_ids.strip():
        body["pageObjectIds"] = [s.strip() for s in slide_ids.split(",") if s.strip()]
    return _submit(presentation_id, {"replaceAllText": body})


def update_text_style(
    presentation_id: str,
    object_id: str,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    strikethrough: bool = False,
    color_hex: str = "",
    size_pt: int = 0,
    font: str = "",
    range_type: str = "ALL",
    start: int = 0,
    end: int = 0,
) -> dict:
    """Update the style of text within an element.

    Only fields explicitly set (non-empty / non-zero) are updated.

    Args:
        presentation_id: The presentation to edit.
        object_id: The objectId of the element containing text.
        bold: Set True to bold.
        italic: Set True to italicize.
        underline: Set True to underline.
        strikethrough: Set True to strikethrough.
        color_hex: Hex text color '#RRGGBB'. Empty = keep current.
        size_pt: Font size in points. 0 = keep current.
        font: Font family (e.g. 'Arial', 'Montserrat'). Empty = keep current.
        range_type: ALL (default), FIXED_RANGE, or FROM_START_INDEX.
        start, end: Character indices for sub-range styling.

    Returns:
        dict with status.
    """
    style: Dict[str, Any] = {}
    fields: List[str] = []

    if bold:
        style["bold"] = True
        fields.append("bold")
    if italic:
        style["italic"] = True
        fields.append("italic")
    if underline:
        style["underline"] = True
        fields.append("underline")
    if strikethrough:
        style["strikethrough"] = True
        fields.append("strikethrough")
    if color_hex:
        style["foregroundColor"] = slides_schema.opaque_color_from_hex(color_hex)
        fields.append("foregroundColor")
    if size_pt > 0:
        style["fontSize"] = {"magnitude": int(size_pt), "unit": "PT"}
        fields.append("fontSize")
    if font:
        style["fontFamily"] = font
        fields.append("fontFamily")

    if not fields:
        return {
            "status": "error",
            "error": "No style fields set — specify at least one of bold/italic/color_hex/size_pt/font.",
            "object_id": object_id,
        }

    text_range: Dict[str, Any] = {"type": range_type}
    if range_type != "ALL":
        text_range["startIndex"] = int(start)
    if range_type == "FIXED_RANGE":
        text_range["endIndex"] = int(end)

    body = {
        "objectId": object_id,
        "style": style,
        "textRange": text_range,
        "fields": ",".join(fields),
    }
    return _submit(presentation_id, {"updateTextStyle": body}, object_id=object_id)


def set_paragraph_style(
    presentation_id: str,
    object_id: str,
    alignment: str = "",
    line_spacing: float = 0.0,
    space_above_pt: float = 0.0,
    space_below_pt: float = 0.0,
    range_type: str = "ALL",
    start: int = 0,
    end: int = 0,
) -> dict:
    """Update paragraph-level style (alignment, spacing).

    Args:
        presentation_id: The presentation to edit.
        object_id: The objectId of the element containing text.
        alignment: START, CENTER, END, or JUSTIFIED. Empty = keep current.
        line_spacing: Line-spacing multiplier (e.g. 1.5 = 1.5x). 0 = keep current.
        space_above_pt: Space above paragraph in points. 0 = keep current.
        space_below_pt: Space below paragraph in points. 0 = keep current.
        range_type: ALL / FIXED_RANGE / FROM_START_INDEX.
        start, end: Character indices.

    Returns:
        dict with status.
    """
    style: Dict[str, Any] = {}
    fields: List[str] = []

    if alignment:
        style["alignment"] = alignment
        fields.append("alignment")
    if line_spacing > 0:
        style["lineSpacing"] = float(line_spacing)
        fields.append("lineSpacing")
    if space_above_pt > 0:
        style["spaceAbove"] = {"magnitude": float(space_above_pt), "unit": "PT"}
        fields.append("spaceAbove")
    if space_below_pt > 0:
        style["spaceBelow"] = {"magnitude": float(space_below_pt), "unit": "PT"}
        fields.append("spaceBelow")

    if not fields:
        return {
            "status": "error",
            "error": "No paragraph style set — specify alignment, line_spacing, or spacing.",
            "object_id": object_id,
        }

    text_range: Dict[str, Any] = {"type": range_type}
    if range_type != "ALL":
        text_range["startIndex"] = int(start)
    if range_type == "FIXED_RANGE":
        text_range["endIndex"] = int(end)

    body = {
        "objectId": object_id,
        "style": style,
        "textRange": text_range,
        "fields": ",".join(fields),
    }
    return _submit(presentation_id, {"updateParagraphStyle": body}, object_id=object_id)


def add_bullets(
    presentation_id: str,
    object_id: str,
    preset: str = "BULLET_DISC_CIRCLE_SQUARE",
    range_type: str = "ALL",
    start: int = 0,
    end: int = 0,
) -> dict:
    """Add bullets to lines of text within an element.

    Args:
        presentation_id: The presentation to edit.
        object_id: The objectId of the element containing text.
        preset: BULLET_DISC_CIRCLE_SQUARE, BULLET_ARROW_DIAMOND_DISC,
                BULLET_STAR_CIRCLE_SQUARE, NUMBERED_DIGIT_ALPHA_ROMAN, ...
        range_type: ALL / FIXED_RANGE / FROM_START_INDEX.
        start, end: Character indices.

    Returns:
        dict with status.
    """
    text_range: Dict[str, Any] = {"type": range_type}
    if range_type != "ALL":
        text_range["startIndex"] = int(start)
    if range_type == "FIXED_RANGE":
        text_range["endIndex"] = int(end)
    body = {"objectId": object_id, "textRange": text_range, "bulletPreset": preset}
    return _submit(presentation_id, {"createParagraphBullets": body}, object_id=object_id)


# ---------------------------------------------------------------------------
# ELEMENT TRANSFORMS & STYLING
# ---------------------------------------------------------------------------


def move_element(
    presentation_id: str,
    object_id: str,
    x: int = DEFAULT_X,
    y: int = DEFAULT_Y,
) -> dict:
    """Move an element to an absolute (x, y) position, preserving its size.

    Args:
        presentation_id: The presentation to edit.
        object_id: The objectId of the element.
        x, y: New top-left position in EMU.

    Returns:
        dict with status.
    """
    body = {
        "objectId": object_id,
        "applyMode": "ABSOLUTE",
        "transform": _emu_transform(x, y, 1.0, 1.0),
    }
    return _submit(presentation_id, {"updatePageElementTransform": body}, object_id=object_id)


def resize_element(
    presentation_id: str,
    object_id: str,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    x: int = DEFAULT_X,
    y: int = DEFAULT_Y,
) -> dict:
    """Resize an element by scaling. Applies an ABSOLUTE transform so you must
    also pass the current x, y (from get_presentation_state) to preserve position.

    Args:
        presentation_id: The presentation to edit.
        object_id: The objectId of the element.
        scale_x: Horizontal scale factor (1.0 = same).
        scale_y: Vertical scale factor (1.0 = same).
        x, y: Top-left in EMU — pass the element's current translateX/Y.

    Returns:
        dict with status.
    """
    body = {
        "objectId": object_id,
        "applyMode": "ABSOLUTE",
        "transform": _emu_transform(x, y, float(scale_x), float(scale_y)),
    }
    return _submit(presentation_id, {"updatePageElementTransform": body}, object_id=object_id)


def delete_element(presentation_id: str, object_id: str) -> dict:
    """Delete an element (or a whole slide) by objectId.

    Args:
        presentation_id: The presentation to edit.
        object_id: The objectId to delete.

    Returns:
        dict with status.
    """
    return _submit(presentation_id, {"deleteObject": {"objectId": object_id}}, object_id=object_id)


def duplicate_element(presentation_id: str, object_id: str) -> dict:
    """Duplicate an element (or a slide).

    Args:
        presentation_id: The presentation to edit.
        object_id: The objectId to duplicate.

    Returns:
        dict with status.
    """
    return _submit(presentation_id, {"duplicateObject": {"objectId": object_id}}, object_id=object_id)


def set_element_color(
    presentation_id: str,
    object_id: str,
    fill_color_hex: str = "",
    outline_color_hex: str = "",
    outline_weight_pt: float = 0.0,
) -> dict:
    """Set shape fill and/or outline color.

    Args:
        presentation_id: The presentation to edit.
        object_id: The objectId of the shape.
        fill_color_hex: Hex fill color '#RRGGBB'. Empty = no change.
        outline_color_hex: Hex outline color '#RRGGBB'. Empty = no change.
        outline_weight_pt: Outline weight in points. 0 = no change.

    Returns:
        dict with status.
    """
    shape_props: Dict[str, Any] = {}
    fields: List[str] = []

    if fill_color_hex:
        shape_props["shapeBackgroundFill"] = slides_schema.solid_fill_from_hex(fill_color_hex)
        fields.append("shapeBackgroundFill.solidFill.color")
    if outline_color_hex or outline_weight_pt > 0:
        outline: Dict[str, Any] = {}
        if outline_color_hex:
            outline["outlineFill"] = slides_schema.solid_fill_from_hex(outline_color_hex)
            fields.append("outline.outlineFill.solidFill.color")
        if outline_weight_pt > 0:
            outline["weight"] = {"magnitude": float(outline_weight_pt), "unit": "PT"}
            fields.append("outline.weight")
        shape_props["outline"] = outline

    if not fields:
        return {
            "status": "error",
            "error": "Specify at least one of fill_color_hex / outline_color_hex / outline_weight_pt.",
            "object_id": object_id,
        }

    body = {
        "objectId": object_id,
        "shapeProperties": shape_props,
        "fields": ",".join(fields),
    }
    return _submit(presentation_id, {"updateShapeProperties": body}, object_id=object_id)


# ---------------------------------------------------------------------------
# TABLE OPERATIONS
# ---------------------------------------------------------------------------


def insert_table_row(
    presentation_id: str,
    table_id: str,
    row: int,
    column: int,
    below: bool = True,
    count: int = 1,
) -> dict:
    """Insert row(s) into a table, above or below a reference cell.

    Args:
        presentation_id: The presentation to edit.
        table_id: The table's objectId.
        row, column: The reference cell coordinates.
        below: True = insert below, False = above.
        count: Number of rows to insert.

    Returns:
        dict with status.
    """
    body = {
        "tableObjectId": table_id,
        "cellLocation": {"rowIndex": int(row), "columnIndex": int(column)},
        "insertBelow": bool(below),
        "number": int(count),
    }
    return _submit(presentation_id, {"insertTableRows": body}, object_id=table_id)


def insert_table_column(
    presentation_id: str,
    table_id: str,
    row: int,
    column: int,
    right: bool = True,
    count: int = 1,
) -> dict:
    """Insert column(s) into a table, left or right of a reference cell.

    Args:
        presentation_id: The presentation to edit.
        table_id: The table's objectId.
        row, column: The reference cell coordinates.
        right: True = insert to the right, False = left.
        count: Number of columns to insert.

    Returns:
        dict with status.
    """
    body = {
        "tableObjectId": table_id,
        "cellLocation": {"rowIndex": int(row), "columnIndex": int(column)},
        "insertRight": bool(right),
        "number": int(count),
    }
    return _submit(presentation_id, {"insertTableColumns": body}, object_id=table_id)


def delete_table_row(presentation_id: str, table_id: str, row: int, column: int = 0) -> dict:
    """Delete one row from a table.

    Args:
        presentation_id: The presentation to edit.
        table_id: The table's objectId.
        row, column: Cell coordinates identifying the row.

    Returns:
        dict with status.
    """
    body = {
        "tableObjectId": table_id,
        "cellLocation": {"rowIndex": int(row), "columnIndex": int(column)},
    }
    return _submit(presentation_id, {"deleteTableRow": body}, object_id=table_id)


def delete_table_column(presentation_id: str, table_id: str, row: int, column: int) -> dict:
    """Delete one column from a table.

    Args:
        presentation_id: The presentation to edit.
        table_id: The table's objectId.
        row, column: Cell coordinates identifying the column.

    Returns:
        dict with status.
    """
    body = {
        "tableObjectId": table_id,
        "cellLocation": {"rowIndex": int(row), "columnIndex": int(column)},
    }
    return _submit(presentation_id, {"deleteTableColumn": body}, object_id=table_id)


def set_cell_background(
    presentation_id: str,
    table_id: str,
    row_start: int,
    col_start: int,
    row_span: int,
    col_span: int,
    color_hex: str,
) -> dict:
    """Set the background color of a cell or rectangular cell range.

    Args:
        presentation_id: The presentation to edit.
        table_id: The table's objectId.
        row_start, col_start: Top-left cell of the range (0-based).
        row_span, col_span: Number of rows/cols to include (1 for a single cell).
        color_hex: Hex fill color '#RRGGBB'.

    Returns:
        dict with status.
    """
    body = {
        "objectId": table_id,
        "tableRange": {
            "location": {"rowIndex": int(row_start), "columnIndex": int(col_start)},
            "rowSpan": int(row_span),
            "columnSpan": int(col_span),
        },
        "tableCellProperties": {
            "tableCellBackgroundFill": slides_schema.solid_fill_from_hex(color_hex),
        },
        "fields": "tableCellBackgroundFill.solidFill.color",
    }
    return _submit(presentation_id, {"updateTableCellProperties": body}, object_id=table_id)


def merge_cells(
    presentation_id: str,
    table_id: str,
    row_start: int,
    col_start: int,
    row_span: int,
    col_span: int,
) -> dict:
    """Merge a rectangular range of table cells.

    Args:
        presentation_id: The presentation to edit.
        table_id: The table's objectId.
        row_start, col_start: Top-left cell.
        row_span, col_span: Range size.

    Returns:
        dict with status.
    """
    body = {
        "objectId": table_id,
        "tableRange": {
            "location": {"rowIndex": int(row_start), "columnIndex": int(col_start)},
            "rowSpan": int(row_span),
            "columnSpan": int(col_span),
        },
    }
    return _submit(presentation_id, {"mergeTableCells": body}, object_id=table_id)


def unmerge_cells(
    presentation_id: str,
    table_id: str,
    row_start: int,
    col_start: int,
    row_span: int,
    col_span: int,
) -> dict:
    """Unmerge previously-merged cells in a table.

    Args:
        presentation_id: The presentation to edit.
        table_id: The table's objectId.
        row_start, col_start: Top-left cell of the merged range.
        row_span, col_span: Range size.

    Returns:
        dict with status.
    """
    body = {
        "objectId": table_id,
        "tableRange": {
            "location": {"rowIndex": int(row_start), "columnIndex": int(col_start)},
            "rowSpan": int(row_span),
            "columnSpan": int(col_span),
        },
    }
    return _submit(presentation_id, {"unmergeTableCells": body}, object_id=table_id)


# ---------------------------------------------------------------------------
# LINE STYLING
# ---------------------------------------------------------------------------


def set_line_style(
    presentation_id: str,
    object_id: str,
    weight_pt: float = 0.0,
    dash_style: str = "",
    color_hex: str = "",
) -> dict:
    """Set the weight, dash style, and color of a line.

    Args:
        presentation_id: The presentation to edit.
        object_id: The line's objectId.
        weight_pt: Line weight in points. 0 = no change.
        dash_style: SOLID, DOT, DASH, DASH_DOT, LONG_DASH, LONG_DASH_DOT. Empty = no change.
        color_hex: Hex line color '#RRGGBB'. Empty = no change.

    Returns:
        dict with status.
    """
    props: Dict[str, Any] = {}
    fields: List[str] = []
    if weight_pt > 0:
        props["weight"] = {"magnitude": float(weight_pt), "unit": "PT"}
        fields.append("weight")
    if dash_style:
        props["dashStyle"] = dash_style
        fields.append("dashStyle")
    if color_hex:
        props["lineFill"] = slides_schema.solid_fill_from_hex(color_hex)
        fields.append("lineFill.solidFill.color")

    if not fields:
        return {
            "status": "error",
            "error": "Specify at least one of weight_pt / dash_style / color_hex.",
            "object_id": object_id,
        }

    body = {
        "objectId": object_id,
        "lineProperties": props,
        "fields": ",".join(fields),
    }
    return _submit(presentation_id, {"updateLineProperties": body}, object_id=object_id)


# ---------------------------------------------------------------------------
# COMMIT (Mode A only — flushes the session buffer to one batchUpdate)
# ---------------------------------------------------------------------------


# Request types that create a new page-element or slide. These change the set
# of valid objectIds, so they must run before any content request that
# references them. Within the structural phase, order still matters (slide
# N's creation must precede a createShape on slide N).
_STRUCTURAL_TYPES = frozenset({
    "createSlide",
    "createShape",
    "createTable",
    "createLine",
    "createImage",
    "createVideo",
    "createSheetsChart",
})


def _extract_object_id(request: Dict[str, Any]) -> Optional[str]:
    """Return the objectId the request targets, or None if it's unscoped.

    Used for Mode C (parallel): requests that share an objectId are grouped
    into the same serial batchUpdate so write ordering on that object is
    preserved. Unscoped requests (e.g. replaceAllText across pages) run in
    their own serial group.
    """
    if not isinstance(request, dict) or len(request) != 1:
        return None
    body = next(iter(request.values()))
    if not isinstance(body, dict):
        return None
    # The Slides API uses varying keys for the target object across request
    # types — objectId, tableObjectId, pageObjectId nested inside elementProperties.
    for key in ("objectId", "tableObjectId"):
        v = body.get(key)
        if isinstance(v, str) and v:
            return v
    elem_props = body.get("elementProperties")
    if isinstance(elem_props, dict):
        page_id = elem_props.get("pageObjectId")
        if isinstance(page_id, str) and page_id:
            return page_id
    return None


def _group_content_by_object(
    content: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Group content requests by objectId so same-id writes stay ordered.

    Requests with no extractable objectId go into a single `_unscoped` group.
    Insertion order is preserved within each group.
    """
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for req in content:
        oid = _extract_object_id(req) or "_unscoped"
        groups[oid].append(req)
    return groups


def _parallel_commit(
    presentation_id: str,
    requests: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Mode C: structural batch first, then parallel per-objectId content batches.

    Uses a ThreadPoolExecutor so the blocking googleapiclient HTTP calls fan
    out across threads. Google Slides `batchUpdate` is atomic per call; we
    preserve that atomicity within each objectId group and across the
    structural phase.
    """
    structural = [r for r in requests if next(iter(r.keys())) in _STRUCTURAL_TYPES]
    content = [r for r in requests if next(iter(r.keys())) not in _STRUCTURAL_TYPES]

    start = time.perf_counter()
    results: List[Dict[str, Any]] = []

    # Phase 1: structural as one serial batchUpdate (order matters).
    if structural:
        try:
            r = slidemakr.execute_slide_requests(presentation_id, structural)
            results.append(r)
        except Exception as e:  # noqa: BLE001
            logger.error(f"parallel commit — structural phase failed: {e}")
            return {
                "status": "error",
                "error": str(e),
                "phase": "structural",
                "pending_requests_dropped": len(requests),
            }

    # Phase 2: content fanned out by objectId — each group runs as its own
    # one-shot batchUpdate; groups run in parallel via a thread pool.
    if content:
        groups = _group_content_by_object(content)
        max_workers = min(len(groups), 10)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(slidemakr.execute_slide_requests, presentation_id, group_reqs)
                for group_reqs in groups.values()
            ]
            for fut in futures:
                try:
                    results.append(fut.result())
                except Exception as e:  # noqa: BLE001
                    logger.error(f"parallel commit — content group failed: {e}")
                    results.append({"status": "error", "error": str(e)})

    elapsed_s = round(time.perf_counter() - start, 3)

    # Aggregate
    total = sum(r.get("total", 0) for r in results)
    successes = sum(r.get("success_count", 0) for r in results)
    errors: List[Any] = []
    for r in results:
        if r.get("errors"):
            errors.extend(r["errors"])
        if r.get("status") == "error" and r.get("error"):
            errors.append({"error": r["error"]})
    error_count = len(errors)

    return {
        "status": "success" if error_count == 0 else ("partial_failure" if successes else "all_failed"),
        "mode": "parallel",
        "structural_count": len(structural),
        "content_groups": len(_group_content_by_object(content)) if content else 0,
        "total": total,
        "success_count": successes,
        "error_count": error_count,
        "errors": errors,
        "elapsed_s": elapsed_s,
        "url": f"https://docs.google.com/presentation/d/{presentation_id}/edit",
    }


def commit_edits(presentation_id: str) -> dict:
    """Flush all queued narrow-tool edits to Google.

    Call this EXACTLY ONCE at the end of every edit turn. If the buffer is
    empty (no narrow tools were called this turn), this is a no-op.

    Execution depends on SLIDEMAKR_BATCH_MODE:
      - "commit" (default): ONE batchUpdate with the full request list.
      - "immediate": no-op (tools already executed eagerly).
      - "parallel": structural batchUpdate first, then parallel per-objectId
        content batchUpdates via a thread pool.

    Args:
        presentation_id: The presentation whose buffer to flush.

    Returns:
        dict with success_count, error_count, errors, and a verification summary.
    """
    if slide_batch.BATCH_MODE == "immediate":
        return {
            "status": "noop",
            "reason": "BATCH_MODE=immediate — edits are already applied as you call each tool.",
        }

    requests = slide_batch.drain(presentation_id)
    if not requests:
        return {
            "status": "noop",
            "reason": "No pending edits to commit.",
        }

    if slide_batch.BATCH_MODE == "parallel":
        result = _parallel_commit(presentation_id, requests)
    else:
        try:
            result = slidemakr.execute_slide_requests(presentation_id, requests)
        except Exception as e:
            logger.error(f"commit_edits failed: {e}")
            return {
                "status": "error",
                "error": str(e),
                "pending_requests_dropped": len(requests),
            }

    # Add a short verification summary so the LLM can confirm changes landed.
    try:
        state = slidemakr.get_presentation_state(presentation_id)
        slide_count = state.get("slide_count", 0)
        slide_titles = []
        for s in state.get("slides", [])[:5]:
            for e in s.get("elements", []):
                if e.get("placeholder") in ("TITLE", "CENTERED_TITLE") and e.get("text"):
                    slide_titles.append(e["text"][:80])
                    break
        result["verification"] = {
            "slide_count": slide_count,
            "first_titles": slide_titles,
        }
    except Exception:  # noqa: BLE001 — verification is best-effort
        pass

    result["committed_request_count"] = len(requests)
    return result
