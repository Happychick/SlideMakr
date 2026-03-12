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


class SlideProperties(BaseModel):
    pageBackgroundFill: Optional[PageBackgroundFill] = None
    layoutObjectId: Optional[str] = None
    masterObjectId: Optional[str] = None
    isSkipped: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def fix_page_properties(cls, data: Any) -> Any:
        """Auto-fix: agent uses 'pageProperties' instead of 'pageBackgroundFill'."""
        if isinstance(data, dict):
            if "pageProperties" in data and "pageBackgroundFill" not in data:
                pp = data.pop("pageProperties")
                if isinstance(pp, dict) and "pageBackgroundFill" in pp:
                    data["pageBackgroundFill"] = pp["pageBackgroundFill"]
                else:
                    # Agent put the fill directly in pageProperties
                    data["pageBackgroundFill"] = pp
        return data


class UpdateSlideProperties(BaseModel):
    objectId: str
    slideProperties: SlideProperties
    fields: str

    @model_validator(mode="before")
    @classmethod
    def fix_fields(cls, data: Any) -> Any:
        """Auto-fix fields referencing pageProperties → pageBackgroundFill."""
        if isinstance(data, dict) and "fields" in data:
            data["fields"] = data["fields"].replace("pageProperties", "pageBackgroundFill")
        return data


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


def validate_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and auto-fix a single Google Slides API request.

    Parses the request through the appropriate Pydantic model, which
    auto-corrects common agent mistakes via validators.

    Unknown request types are passed through with only recursive color fixing.

    Returns:
        The validated/fixed request dict ready for batchUpdate.
    """
    if not isinstance(request, dict) or len(request) != 1:
        # Pass through malformed requests (let the API return the error)
        return _fix_color_recursive(request)

    req_type = next(iter(request))
    req_body = request[req_type]

    model_cls = REQUEST_MODELS.get(req_type)
    if model_cls is None:
        # Unknown request type — just fix colors recursively
        logger.debug(f"No schema for request type '{req_type}', applying color fix only")
        return {req_type: _fix_color_recursive(req_body)}

    try:
        validated = model_cls.model_validate(req_body)
        # Convert back to dict, excluding None values to keep requests clean
        fixed = validated.model_dump(exclude_none=True)
        return {req_type: fixed}
    except Exception as e:
        # Validation failed — log warning and fall back to color fix only
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
    for i, req in enumerate(requests):
        try:
            fixed.append(validate_request(req))
        except Exception as e:
            logger.error(f"Request {i} validation crashed: {e}. Passing through raw.")
            fixed.append(req)
    return fixed
