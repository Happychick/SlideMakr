"""
SlideMakr - Pydantic Models for Google Slides API Request Validation

Validates and auto-fixes agent-generated Google Slides API requests before
sending them to batchUpdate. Common agent mistakes (wrong color format,
missing wrappers, wrong field names) are auto-corrected by validators.

Usage:
    from .slides_schema import validate_requests
    fixed = validate_requests(raw_requests)  # list[dict] → list[dict]
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

# ============================================================================
# SHARED TYPES
# ============================================================================


class Dimension(BaseModel):
    magnitude: float = 0
    unit: Literal["EMU", "PT"] = "EMU"


class Size(BaseModel):
    width: Optional[Dimension] = None
    height: Optional[Dimension] = None


class AffineTransform(BaseModel):
    scaleX: float = 1
    scaleY: float = 1
    shearX: float = 0
    shearY: float = 0
    translateX: float = 0
    translateY: float = 0
    unit: Literal["EMU", "PT"] = "EMU"


class PageElementProperties(BaseModel):
    pageObjectId: str
    size: Optional[Size] = None
    transform: Optional[AffineTransform] = None


class RgbColor(BaseModel):
    red: float = 0.0
    green: float = 0.0
    blue: float = 0.0


class OpaqueColor(BaseModel):
    rgbColor: Optional[RgbColor] = None
    themeColor: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def fix_bare_color(cls, data: Any) -> Any:
        """Auto-fix bare {red, green, blue} passed directly as OpaqueColor."""
        if isinstance(data, dict):
            keys = set(data.keys())
            # Bare color: {red: 0.5, green: 0.3, blue: 0.1}
            if keys <= {"red", "green", "blue"} and len(keys) >= 2:
                return {"rgbColor": data}
        return data


class OptionalColor(BaseModel):
    opaqueColor: Optional[OpaqueColor] = None

    @model_validator(mode="before")
    @classmethod
    def fix_missing_wrapper(cls, data: Any) -> Any:
        """Auto-fix when agent passes rgbColor or bare color directly."""
        if isinstance(data, dict):
            keys = set(data.keys())
            # Bare color: {red, green, blue}
            if keys <= {"red", "green", "blue"} and len(keys) >= 2:
                return {"opaqueColor": {"rgbColor": data}}
            # Missing opaqueColor wrapper: {rgbColor: {...}}
            if "rgbColor" in data and "opaqueColor" not in data:
                return {"opaqueColor": data}
            # Missing opaqueColor wrapper: {themeColor: "..."}
            if "themeColor" in data and "opaqueColor" not in data:
                return {"opaqueColor": data}
        return data


class SolidFill(BaseModel):
    color: Optional[OpaqueColor] = None
    alpha: Optional[float] = None

    @model_validator(mode="before")
    @classmethod
    def fix_color_in_solid_fill(cls, data: Any) -> Any:
        """Auto-fix when agent passes bare color as solidFill.color."""
        if isinstance(data, dict) and "color" in data:
            color = data["color"]
            if isinstance(color, dict):
                keys = set(color.keys())
                # Bare RGB directly in color
                if keys <= {"red", "green", "blue"} and len(keys) >= 2:
                    data = {**data, "color": {"rgbColor": color}}
        return data


class TextRange(BaseModel):
    type: Literal["ALL", "FIXED_RANGE", "FROM_START_INDEX"] = "ALL"
    startIndex: Optional[int] = None
    endIndex: Optional[int] = None


# ============================================================================
# SLIDE OPERATIONS
# ============================================================================


class SlideLayoutReference(BaseModel):
    predefinedLayout: Optional[str] = None
    layoutId: Optional[str] = None


class CreateSlide(BaseModel):
    objectId: Optional[str] = None
    insertionIndex: Optional[int] = None
    slideLayoutReference: Optional[SlideLayoutReference] = None
    placeholderIdMappings: Optional[List[Dict[str, Any]]] = None


class UpdateSlidesPosition(BaseModel):
    slideObjectIds: List[str]
    insertionIndex: int


class DeleteObject(BaseModel):
    objectId: str


class DuplicateObject(BaseModel):
    objectId: str
    objectIds: Optional[Dict[str, str]] = None


# ============================================================================
# SHAPE OPERATIONS
# ============================================================================


class CreateShape(BaseModel):
    objectId: Optional[str] = None
    shapeType: str = "TEXT_BOX"
    elementProperties: PageElementProperties


class ShapeBackgroundFill(BaseModel):
    solidFill: Optional[SolidFill] = None
    propertyState: Optional[str] = None


class OutlineFill(BaseModel):
    solidFill: Optional[SolidFill] = None


class Outline(BaseModel):
    outlineFill: Optional[OutlineFill] = None
    weight: Optional[Dimension] = None
    dashStyle: Optional[str] = None
    propertyState: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def fix_outline(cls, data: Any) -> Any:
        """Auto-fix solidFill directly in outline → outline.outlineFill.solidFill."""
        if isinstance(data, dict) and "solidFill" in data and "outlineFill" not in data:
            sf = data.pop("solidFill")
            data["outlineFill"] = {"solidFill": sf}
        return data


class ShapeProperties(BaseModel):
    shapeBackgroundFill: Optional[ShapeBackgroundFill] = None
    outline: Optional[Outline] = None
    shadow: Optional[Dict[str, Any]] = None
    link: Optional[Dict[str, Any]] = None
    contentAlignment: Optional[str] = None
    autofit: Optional[Dict[str, Any]] = None


class UpdateShapeProperties(BaseModel):
    objectId: str
    shapeProperties: ShapeProperties
    fields: str

    @model_validator(mode="before")
    @classmethod
    def fix_shape_props(cls, data: Any) -> Any:
        """Auto-fix common mistakes in shape property updates."""
        if isinstance(data, dict):
            props = data.get("shapeProperties", {})
            # Fix: agent uses 'backgroundFill' instead of 'shapeBackgroundFill'
            if isinstance(props, dict) and "backgroundFill" in props and "shapeBackgroundFill" not in props:
                props["shapeBackgroundFill"] = props.pop("backgroundFill")
                data["shapeProperties"] = props
        return data


# ============================================================================
# TEXT OPERATIONS
# ============================================================================


class CellLocation(BaseModel):
    rowIndex: int
    columnIndex: int


class InsertText(BaseModel):
    objectId: str
    text: str = ""
    insertionIndex: Optional[int] = 0
    cellLocation: Optional[CellLocation] = None


class DeleteText(BaseModel):
    objectId: str
    textRange: Optional[TextRange] = None
    cellLocation: Optional[CellLocation] = None

    @model_validator(mode="before")
    @classmethod
    def default_text_range(cls, data: Any) -> Any:
        """Default to ALL if no textRange specified."""
        if isinstance(data, dict) and "textRange" not in data:
            data["textRange"] = {"type": "ALL"}
        return data


class TextStyle(BaseModel):
    foregroundColor: Optional[OptionalColor] = None
    backgroundColor: Optional[OptionalColor] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    strikethrough: Optional[bool] = None
    fontFamily: Optional[str] = None
    fontSize: Optional[Dimension] = None
    link: Optional[Dict[str, Any]] = None
    baselineOffset: Optional[str] = None
    smallCaps: Optional[bool] = None
    weightedFontFamily: Optional[Dict[str, Any]] = None

    @model_validator(mode="before")
    @classmethod
    def fix_text_colors(cls, data: Any) -> Any:
        """Auto-fix color formats in text style."""
        if not isinstance(data, dict):
            return data
        for color_field in ("foregroundColor", "backgroundColor"):
            if color_field in data:
                val = data[color_field]
                if isinstance(val, dict):
                    keys = set(val.keys())
                    # Bare RGB
                    if keys <= {"red", "green", "blue"} and len(keys) >= 2:
                        data[color_field] = {"opaqueColor": {"rgbColor": val}}
                    # rgbColor without opaqueColor wrapper
                    elif "rgbColor" in val and "opaqueColor" not in val:
                        data[color_field] = {"opaqueColor": val}
        return data


class UpdateTextStyle(BaseModel):
    objectId: str
    style: TextStyle
    textRange: Optional[TextRange] = None
    cellLocation: Optional[CellLocation] = None
    fields: str

    @model_validator(mode="before")
    @classmethod
    def default_text_range(cls, data: Any) -> Any:
        if isinstance(data, dict) and "textRange" not in data:
            data["textRange"] = {"type": "ALL"}
        return data


class ParagraphStyle(BaseModel):
    alignment: Optional[str] = None
    lineSpacing: Optional[float] = None
    spaceAbove: Optional[Dimension] = None
    spaceBelow: Optional[Dimension] = None
    indentStart: Optional[Dimension] = None
    indentEnd: Optional[Dimension] = None
    indentFirstLine: Optional[Dimension] = None
    direction: Optional[str] = None
    spacingMode: Optional[str] = None


class UpdateParagraphStyle(BaseModel):
    objectId: str
    style: ParagraphStyle
    textRange: Optional[TextRange] = None
    cellLocation: Optional[CellLocation] = None
    fields: str

    @model_validator(mode="before")
    @classmethod
    def default_text_range(cls, data: Any) -> Any:
        if isinstance(data, dict) and "textRange" not in data:
            data["textRange"] = {"type": "ALL"}
        return data


class CreateParagraphBullets(BaseModel):
    objectId: str
    textRange: Optional[TextRange] = None
    bulletPreset: str = "BULLET_DISC_CIRCLE_SQUARE"
    cellLocation: Optional[CellLocation] = None

    @model_validator(mode="before")
    @classmethod
    def default_text_range(cls, data: Any) -> Any:
        if isinstance(data, dict) and "textRange" not in data:
            data["textRange"] = {"type": "ALL"}
        return data


class ReplaceAllText(BaseModel):
    containsText: Dict[str, Any]
    replaceText: str
    pageObjectIds: Optional[List[str]] = None


# ============================================================================
# TABLE OPERATIONS
# ============================================================================


class CreateTable(BaseModel):
    objectId: Optional[str] = None
    elementProperties: PageElementProperties
    rows: int
    columns: int


class InsertTableRows(BaseModel):
    tableObjectId: str
    cellLocation: CellLocation
    insertBelow: bool = True
    number: int = 1


class InsertTableColumns(BaseModel):
    tableObjectId: str
    cellLocation: CellLocation
    insertRight: bool = True
    number: int = 1


class DeleteTableRow(BaseModel):
    tableObjectId: str
    cellLocation: CellLocation


class DeleteTableColumn(BaseModel):
    tableObjectId: str
    cellLocation: CellLocation


class TableCellProperties(BaseModel):
    tableCellBackgroundFill: Optional[Dict[str, Any]] = None
    contentAlignment: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def fix_cell_bg(cls, data: Any) -> Any:
        """Auto-fix background fill in table cells."""
        if isinstance(data, dict):
            bg = data.get("tableCellBackgroundFill", {})
            if isinstance(bg, dict):
                data["tableCellBackgroundFill"] = _fix_color_recursive(bg)
        return data


class MergeTableCells(BaseModel):
    objectId: str
    tableRange: Dict[str, Any]


class UnmergeTableCells(BaseModel):
    objectId: str
    tableRange: Dict[str, Any]


class UpdateTableCellProperties(BaseModel):
    objectId: str
    tableRange: Optional[Dict[str, Any]] = None
    tableCellProperties: TableCellProperties
    fields: str


# ============================================================================
# IMAGE & LINE OPERATIONS
# ============================================================================


class CreateImage(BaseModel):
    objectId: Optional[str] = None
    url: str
    elementProperties: PageElementProperties


class CreateLine(BaseModel):
    objectId: Optional[str] = None
    lineCategory: Optional[str] = "STRAIGHT"
    elementProperties: PageElementProperties
    category: Optional[str] = None  # alias some agents use


class UpdateLineProperties(BaseModel):
    objectId: str
    lineProperties: Dict[str, Any]
    fields: str


# ============================================================================
# SLIDE PROPERTIES
# ============================================================================


class PageBackgroundFill(BaseModel):
    solidFill: Optional[SolidFill] = None
    stretchedPictureFill: Optional[Dict[str, Any]] = None
    propertyState: Optional[str] = None


class PageProperties(BaseModel):
    """Used by updatePageProperties — the correct way to set slide backgrounds."""
    pageBackgroundFill: Optional[PageBackgroundFill] = None
    colorScheme: Optional[Dict[str, Any]] = None

    @model_validator(mode="before")
    @classmethod
    def fix_page_properties(cls, data: Any) -> Any:
        """Auto-fix: agent nests under 'pageProperties' redundantly."""
        if isinstance(data, dict) and "pageProperties" in data and "pageBackgroundFill" not in data:
            pp = data.pop("pageProperties")
            if isinstance(pp, dict) and "pageBackgroundFill" in pp:
                data["pageBackgroundFill"] = pp["pageBackgroundFill"]
            else:
                data["pageBackgroundFill"] = pp
        return data


class UpdatePageProperties(BaseModel):
    """The correct request type for changing slide backgrounds."""
    objectId: str
    pageProperties: PageProperties
    fields: str

    @model_validator(mode="before")
    @classmethod
    def fix_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and "fields" in data:
            data["fields"] = data["fields"].replace("pageProperties.", "")
            # Ensure fields don't have leftover "pageProperties" prefix
        return data


class SlideProperties(BaseModel):
    """Used by updateSlideProperties — only for isSkipped, layout, master."""
    layoutObjectId: Optional[str] = None
    masterObjectId: Optional[str] = None
    isSkipped: Optional[bool] = None


class UpdateSlideProperties(BaseModel):
    objectId: str
    slideProperties: SlideProperties
    fields: str


# ============================================================================
# TRANSFORM OPERATIONS
# ============================================================================


class UpdatePageElementTransform(BaseModel):
    objectId: str
    transform: AffineTransform
    applyMode: str = "RELATIVE"


# ============================================================================
# REQUEST MAPPING & VALIDATION
# ============================================================================

# Maps request type name → Pydantic model
REQUEST_MODELS: Dict[str, type[BaseModel]] = {
    "createSlide": CreateSlide,
    "createShape": CreateShape,
    "createTable": CreateTable,
    "createLine": CreateLine,
    "createImage": CreateImage,
    "insertText": InsertText,
    "deleteText": DeleteText,
    "updateTextStyle": UpdateTextStyle,
    "updateParagraphStyle": UpdateParagraphStyle,
    "updateShapeProperties": UpdateShapeProperties,
    "updateSlideProperties": UpdateSlideProperties,
    "updatePageProperties": UpdatePageProperties,
    "updatePageElementTransform": UpdatePageElementTransform,
    "deleteObject": DeleteObject,
    "duplicateObject": DuplicateObject,
    "replaceAllText": ReplaceAllText,
    "createParagraphBullets": CreateParagraphBullets,
    "updateSlidesPosition": UpdateSlidesPosition,
    "insertTableRows": InsertTableRows,
    "insertTableColumns": InsertTableColumns,
    "deleteTableRow": DeleteTableRow,
    "deleteTableColumn": DeleteTableColumn,
    "updateTableCellProperties": UpdateTableCellProperties,
    "mergeTableCells": MergeTableCells,
    "unmergeTableCells": UnmergeTableCells,
    "updateLineProperties": UpdateLineProperties,
}


def _fix_color_recursive(obj: Any) -> Any:
    """Recursively fix bare {red, green, blue} → {rgbColor: {red, green, blue}}.

    Used as a fallback for request types we don't have models for.
    """
    if isinstance(obj, dict):
        keys = set(obj.keys())
        if keys <= {"red", "green", "blue"} and len(keys) >= 2:
            return {"rgbColor": obj}
        return {k: _fix_color_recursive(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_fix_color_recursive(item) for item in obj]
    return obj


# Request types that the agent hallucinates — drop them silently
INVALID_REQUEST_TYPES = {
    "updatePageElementProperties",  # doesn't exist in the API
}


def _convert_slide_to_page_properties(request: Dict[str, Any]) -> Dict[str, Any]:
    """Convert updateSlideProperties with background → updatePageProperties.

    The agent commonly uses updateSlideProperties to set backgrounds, but the
    Google Slides API requires updatePageProperties for that. slideProperties
    only supports isSkipped, layoutObjectId, masterObjectId.
    """
    body = request["updateSlideProperties"]
    slide_props = body.get("slideProperties", {})

    # Check if the agent is trying to set a background (wrong request type)
    has_bg = (
        "pageBackgroundFill" in slide_props
        or "pageProperties" in slide_props
    )
    if not has_bg:
        return request  # Legit updateSlideProperties (isSkipped etc.)

    # Extract background fill
    if "pageProperties" in slide_props:
        pp = slide_props["pageProperties"]
        if isinstance(pp, dict) and "pageBackgroundFill" in pp:
            bg_fill = pp["pageBackgroundFill"]
        else:
            bg_fill = pp
    else:
        bg_fill = slide_props["pageBackgroundFill"]

    # Build correct updatePageProperties request
    fields = body.get("fields", "pageBackgroundFill.solidFill.color")
    # Strip any leftover "slideProperties." or "pageProperties." from fields
    fields = fields.replace("slideProperties.", "").replace("pageProperties.", "")

    return {
        "updatePageProperties": {
            "objectId": body["objectId"],
            "pageProperties": {
                "pageBackgroundFill": bg_fill,
            },
            "fields": fields,
        }
    }


def validate_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate and auto-fix a single Google Slides API request.

    Parses the request through the appropriate Pydantic model, which
    auto-corrects common agent mistakes via validators. Also converts
    misused request types (e.g. updateSlideProperties for backgrounds).

    Returns None for requests that should be dropped (invalid types).

    Returns:
        The validated/fixed request dict ready for batchUpdate, or None to drop.
    """
    if not isinstance(request, dict) or len(request) != 1:
        return _fix_color_recursive(request)

    req_type = next(iter(request))
    req_body = request[req_type]

    # Drop hallucinated request types
    if req_type in INVALID_REQUEST_TYPES:
        logger.info(f"Dropping invalid request type '{req_type}'")
        return None

    # Convert updateSlideProperties with background → updatePageProperties
    if req_type == "updateSlideProperties":
        request = _convert_slide_to_page_properties(request)
        req_type = next(iter(request))
        req_body = request[req_type]

    model_cls = REQUEST_MODELS.get(req_type)
    if model_cls is None:
        logger.debug(f"No schema for request type '{req_type}', applying color fix only")
        return {req_type: _fix_color_recursive(req_body)}

    try:
        validated = model_cls.model_validate(req_body)
        fixed = validated.model_dump(exclude_none=True)
        return {req_type: fixed}
    except Exception as e:
        logger.warning(f"Schema validation failed for '{req_type}': {e}. Using color fix fallback.")
        return {req_type: _fix_color_recursive(req_body)}


def validate_requests(requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate and auto-fix a list of Google Slides API requests.

    This is the main entry point. Pass the raw request list from the agent,
    get back a cleaned list ready for batchUpdate.

    Args:
        requests: List of raw request dicts from the agent

    Returns:
        List of validated/fixed request dicts
    """
    fixed = []
    dropped = 0
    for i, req in enumerate(requests):
        try:
            result = validate_request(req)
            if result is not None:
                fixed.append(result)
            else:
                dropped += 1
        except Exception as e:
            logger.error(f"Request {i} validation crashed: {e}. Passing through raw.")
            fixed.append(req)
    if dropped:
        logger.info(f"Dropped {dropped} invalid request(s)")
    return fixed
