"""
SlideMakr - Flowchart Generator

Converts a logical flowchart definition (nodes + edges) into Google Slides API
requests. Handles all EMU positioning, shape creation, connector routing, and
text insertion automatically.

The agent calls create_flowchart_on_slide() with a simple structure:
  nodes: [{"id": "start", "label": "Start", "type": "oval"}, ...]
  edges: [{"from": "start", "to": "process1", "label": "Yes"}, ...]

The tool figures out layout, positioning, and generates all the batchUpdate
requests needed.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTS
# ============================================================================

# Slide dimensions in EMU
SLIDE_WIDTH = 9144000   # 10 inches
SLIDE_HEIGHT = 5143500  # ~5.63 inches

# Grid layout constants
MARGIN_X = 500000       # Left/right margin
MARGIN_TOP = 600000     # Top margin (below title area)
MARGIN_BOTTOM = 300000  # Bottom margin

# Node dimensions
NODE_WIDTH = 2200000    # ~2.4 inches
NODE_HEIGHT = 700000    # ~0.77 inches
NODE_H_GAP = 400000     # Horizontal gap between nodes
NODE_V_GAP = 350000     # Vertical gap between rows

# Shape type mapping
SHAPE_TYPES = {
    "oval": "ELLIPSE",           # Start/End
    "ellipse": "ELLIPSE",
    "start": "ELLIPSE",
    "end": "ELLIPSE",
    "terminator": "ELLIPSE",
    "rectangle": "RECTANGLE",    # Process
    "process": "RECTANGLE",
    "box": "RECTANGLE",
    "diamond": "DIAMOND",        # Decision
    "decision": "DIAMOND",
    "condition": "DIAMOND",
    "if": "DIAMOND",
    "rounded": "ROUND_RECTANGLE",  # Subroutine
    "subroutine": "ROUND_RECTANGLE",
    "subprocess": "ROUND_RECTANGLE",
    "parallelogram": "RECTANGLE",  # I/O (no parallelogram in API, use rect)
    "io": "RECTANGLE",
    "input": "RECTANGLE",
    "output": "RECTANGLE",
    "document": "ROUND_RECTANGLE",
}

# Colors for different node types (dark theme)
NODE_COLORS = {
    "ELLIPSE": {"red": 0.2, "green": 0.5, "blue": 0.8},      # Blue for start/end
    "RECTANGLE": {"red": 0.25, "green": 0.25, "blue": 0.35},  # Dark slate for process
    "DIAMOND": {"red": 0.7, "green": 0.4, "blue": 0.2},       # Amber for decisions
    "ROUND_RECTANGLE": {"red": 0.3, "green": 0.5, "blue": 0.3},  # Green for subroutines
}

TEXT_COLOR = {"red": 1.0, "green": 1.0, "blue": 1.0}  # White text
CONNECTOR_COLOR = {"red": 0.6, "green": 0.6, "blue": 0.7}  # Light gray
LABEL_COLOR = {"red": 0.8, "green": 0.8, "blue": 0.8}  # Lighter gray for edge labels
BG_COLOR = {"red": 0.12, "green": 0.12, "blue": 0.18}  # Dark background


# ============================================================================
# LAYOUT ENGINE
# ============================================================================


def _assign_positions(nodes: List[Dict], edges: List[Dict]) -> Dict[str, Dict]:
    """Assign x, y positions to nodes using a simple top-down layout.

    Strategy: BFS from root nodes, assign rows. Within each row, center nodes.
    Falls back to sequential layout if graph structure is unclear.
    """
    node_map = {n["id"]: n for n in nodes}
    node_ids = [n["id"] for n in nodes]

    # Build adjacency: who points to whom
    children = {}
    parents = {}
    for e in edges:
        children.setdefault(e["from"], []).append(e["to"])
        parents.setdefault(e["to"], []).append(e["from"])

    # Find root nodes (no incoming edges)
    roots = [nid for nid in node_ids if nid not in parents]
    if not roots:
        roots = [node_ids[0]] if node_ids else []

    # BFS to assign rows
    row_assignment = {}
    visited = set()
    queue = [(r, 0) for r in roots]

    for nid, row in queue:
        if nid in visited:
            continue
        visited.add(nid)
        # If already assigned to a later row, keep the later one
        row_assignment[nid] = max(row_assignment.get(nid, 0), row)
        for child in children.get(nid, []):
            if child not in visited:
                queue.append((child, row + 1))

    # Add any unvisited nodes (disconnected) to the last row + 1
    max_row = max(row_assignment.values()) if row_assignment else 0
    for nid in node_ids:
        if nid not in row_assignment:
            max_row += 1
            row_assignment[nid] = max_row

    # Group nodes by row
    rows = {}
    for nid, row in row_assignment.items():
        rows.setdefault(row, []).append(nid)

    # Calculate usable area
    usable_width = SLIDE_WIDTH - 2 * MARGIN_X
    usable_height = SLIDE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    num_rows = len(rows)

    # Dynamically size nodes if there are many
    effective_node_w = min(NODE_WIDTH, (usable_width - NODE_H_GAP) // max(max(len(r) for r in rows.values()), 1) - NODE_H_GAP)
    effective_node_w = max(effective_node_w, 1200000)  # Minimum width
    effective_node_h = min(NODE_HEIGHT, (usable_height - NODE_V_GAP) // max(num_rows, 1) - NODE_V_GAP)
    effective_node_h = max(effective_node_h, 450000)  # Minimum height

    # Assign positions
    positions = {}
    for row_idx in sorted(rows.keys()):
        row_nodes = rows[row_idx]
        num_in_row = len(row_nodes)

        # Center this row
        total_row_width = num_in_row * effective_node_w + (num_in_row - 1) * NODE_H_GAP
        start_x = MARGIN_X + (usable_width - total_row_width) // 2

        y = MARGIN_TOP + row_idx * (effective_node_h + NODE_V_GAP)

        for col_idx, nid in enumerate(row_nodes):
            x = start_x + col_idx * (effective_node_w + NODE_H_GAP)
            positions[nid] = {
                "x": x,
                "y": y,
                "w": effective_node_w,
                "h": effective_node_h,
            }

    return positions


# ============================================================================
# REQUEST GENERATION
# ============================================================================


def _uid(prefix: str = "fc") -> str:
    """Generate a short unique ID for flowchart elements."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def generate_flowchart_requests(
    slide_id: str,
    nodes: List[Dict],
    edges: List[Dict],
    title: Optional[str] = None,
    style: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    """Generate Google Slides API requests for a flowchart.

    Args:
        slide_id: The objectId of the slide to draw on
        nodes: List of {"id": str, "label": str, "type": str}
               type is one of: oval/start/end, rectangle/process, diamond/decision, rounded/subroutine
        edges: List of {"from": str, "to": str, "label"?: str}
        title: Optional title text to add at the top
        style: Optional style overrides (bg_color, text_color, etc.)

    Returns:
        List of Google Slides API request dicts
    """
    requests = []
    positions = _assign_positions(nodes, edges)
    node_map = {n["id"]: n for n in nodes}

    # Custom colors from style
    bg = (style or {}).get("bg_color", BG_COLOR)
    txt_color = (style or {}).get("text_color", TEXT_COLOR)
    conn_color = (style or {}).get("connector_color", CONNECTOR_COLOR)

    # 1. Set slide background
    requests.append({
        "updatePageProperties": {
            "objectId": slide_id,
            "pageProperties": {
                "pageBackgroundFill": {
                    "solidFill": {
                        "color": {"rgbColor": bg}
                    }
                }
            },
            "fields": "pageBackgroundFill.solidFill.color"
        }
    })

    # 2. Optional title
    if title:
        title_id = _uid("title")
        requests.append({
            "createShape": {
                "objectId": title_id,
                "shapeType": "TEXT_BOX",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {
                        "width": {"magnitude": SLIDE_WIDTH - 2 * MARGIN_X, "unit": "EMU"},
                        "height": {"magnitude": 450000, "unit": "EMU"},
                    },
                    "transform": {
                        "scaleX": 1, "scaleY": 1,
                        "translateX": MARGIN_X,
                        "translateY": 100000,
                        "unit": "EMU",
                    },
                },
            }
        })
        requests.append({
            "insertText": {
                "objectId": title_id,
                "text": title,
                "insertionIndex": 0,
            }
        })
        requests.append({
            "updateTextStyle": {
                "objectId": title_id,
                "style": {
                    "foregroundColor": {"opaqueColor": {"rgbColor": txt_color}},
                    "fontSize": {"magnitude": 22, "unit": "PT"},
                    "bold": True,
                    "fontFamily": "Inter",
                },
                "textRange": {"type": "ALL"},
                "fields": "foregroundColor,fontSize,bold,fontFamily",
            }
        })
        requests.append({
            "updateParagraphStyle": {
                "objectId": title_id,
                "style": {"alignment": "CENTER"},
                "textRange": {"type": "ALL"},
                "fields": "alignment",
            }
        })

    # 3. Create nodes (shapes + text)
    node_object_ids = {}  # node_id → shape objectId
    for node in nodes:
        nid = node["id"]
        pos = positions.get(nid)
        if not pos:
            continue

        shape_type = SHAPE_TYPES.get(node.get("type", "rectangle"), "RECTANGLE")
        shape_id = _uid("node")
        node_object_ids[nid] = shape_id
        fill_color = NODE_COLORS.get(shape_type, NODE_COLORS["RECTANGLE"])

        # Allow per-node color override
        if "color" in node:
            fill_color = node["color"]

        # Make diamonds taller so text fits
        h = pos["h"]
        w = pos["w"]
        if shape_type == "DIAMOND":
            h = int(h * 1.3)

        requests.append({
            "createShape": {
                "objectId": shape_id,
                "shapeType": shape_type,
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {
                        "width": {"magnitude": w, "unit": "EMU"},
                        "height": {"magnitude": h, "unit": "EMU"},
                    },
                    "transform": {
                        "scaleX": 1, "scaleY": 1,
                        "translateX": pos["x"],
                        "translateY": pos["y"],
                        "unit": "EMU",
                    },
                },
            }
        })

        # Shape fill
        requests.append({
            "updateShapeProperties": {
                "objectId": shape_id,
                "shapeProperties": {
                    "shapeBackgroundFill": {
                        "solidFill": {
                            "color": {"rgbColor": fill_color},
                            "alpha": 1.0,
                        }
                    },
                    "outline": {
                        "outlineFill": {
                            "solidFill": {
                                "color": {"rgbColor": conn_color},
                                "alpha": 0.5,
                            }
                        },
                        "weight": {"magnitude": 1, "unit": "PT"},
                    },
                    "contentAlignment": "MIDDLE",
                },
                "fields": "shapeBackgroundFill,outline,contentAlignment",
            }
        })

        # Insert text
        label = node.get("label", nid)
        requests.append({
            "insertText": {
                "objectId": shape_id,
                "text": label,
                "insertionIndex": 0,
            }
        })

        # Style text
        font_size = 12 if len(label) > 20 else 14
        requests.append({
            "updateTextStyle": {
                "objectId": shape_id,
                "style": {
                    "foregroundColor": {"opaqueColor": {"rgbColor": txt_color}},
                    "fontSize": {"magnitude": font_size, "unit": "PT"},
                    "bold": shape_type == "ELLIPSE",
                    "fontFamily": "Inter",
                },
                "textRange": {"type": "ALL"},
                "fields": "foregroundColor,fontSize,bold,fontFamily",
            }
        })

        # Center text
        requests.append({
            "updateParagraphStyle": {
                "objectId": shape_id,
                "style": {"alignment": "CENTER"},
                "textRange": {"type": "ALL"},
                "fields": "alignment",
            }
        })

    # 4. Create edges (connectors/lines)
    for edge in edges:
        from_id = edge.get("from")
        to_id = edge.get("to")
        from_pos = positions.get(from_id)
        to_pos = positions.get(to_id)
        if not from_pos or not to_pos:
            continue

        line_id = _uid("edge")

        # Calculate line start/end points (center-bottom of source → center-top of target)
        from_cx = from_pos["x"] + from_pos["w"] // 2
        from_by = from_pos["y"] + from_pos["h"]
        to_cx = to_pos["x"] + to_pos["w"] // 2
        to_ty = to_pos["y"]

        # If nodes are on the same row, connect right-to-left instead
        if abs(from_pos["y"] - to_pos["y"]) < NODE_V_GAP // 2:
            # Horizontal: right side of from → left side of to
            if from_pos["x"] < to_pos["x"]:
                from_cx = from_pos["x"] + from_pos["w"]
                from_by = from_pos["y"] + from_pos["h"] // 2
                to_cx = to_pos["x"]
                to_ty = to_pos["y"] + to_pos["h"] // 2
            else:
                from_cx = from_pos["x"]
                from_by = from_pos["y"] + from_pos["h"] // 2
                to_cx = to_pos["x"] + to_pos["w"]
                to_ty = to_pos["y"] + to_pos["h"] // 2

        # Line dimensions (can be negative for direction)
        dx = to_cx - from_cx
        dy = to_ty - from_by

        requests.append({
            "createLine": {
                "objectId": line_id,
                "lineCategory": "STRAIGHT",
                "elementProperties": {
                    "pageObjectId": slide_id,
                    "size": {
                        "width": {"magnitude": abs(dx) or 1, "unit": "EMU"},
                        "height": {"magnitude": abs(dy) or 1, "unit": "EMU"},
                    },
                    "transform": {
                        "scaleX": 1 if dx >= 0 else -1,
                        "scaleY": 1 if dy >= 0 else -1,
                        "translateX": from_cx if dx >= 0 else from_cx + dx,
                        "translateY": from_by if dy >= 0 else from_by + dy,
                        "unit": "EMU",
                    },
                },
            }
        })

        # Style the line
        requests.append({
            "updateLineProperties": {
                "objectId": line_id,
                "lineProperties": {
                    "lineFill": {
                        "solidFill": {
                            "color": {"rgbColor": conn_color},
                        }
                    },
                    "weight": {"magnitude": 2, "unit": "PT"},
                    "endArrow": "OPEN_ARROW",
                },
                "fields": "lineFill,weight,endArrow",
            }
        })

        # Edge label (if provided)
        edge_label = edge.get("label")
        if edge_label:
            label_id = _uid("elbl")
            # Place label at midpoint of the line
            mid_x = (from_cx + to_cx) // 2 - 400000
            mid_y = (from_by + to_ty) // 2 - 200000

            requests.append({
                "createShape": {
                    "objectId": label_id,
                    "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": slide_id,
                        "size": {
                            "width": {"magnitude": 800000, "unit": "EMU"},
                            "height": {"magnitude": 350000, "unit": "EMU"},
                        },
                        "transform": {
                            "scaleX": 1, "scaleY": 1,
                            "translateX": mid_x,
                            "translateY": mid_y,
                            "unit": "EMU",
                        },
                    },
                }
            })
            requests.append({
                "insertText": {
                    "objectId": label_id,
                    "text": edge_label,
                    "insertionIndex": 0,
                }
            })
            requests.append({
                "updateTextStyle": {
                    "objectId": label_id,
                    "style": {
                        "foregroundColor": {"opaqueColor": {"rgbColor": LABEL_COLOR}},
                        "fontSize": {"magnitude": 10, "unit": "PT"},
                        "italic": True,
                        "fontFamily": "Inter",
                    },
                    "textRange": {"type": "ALL"},
                    "fields": "foregroundColor,fontSize,italic,fontFamily",
                }
            })
            requests.append({
                "updateParagraphStyle": {
                    "objectId": label_id,
                    "style": {"alignment": "CENTER"},
                    "textRange": {"type": "ALL"},
                    "fields": "alignment",
                }
            })

    logger.info(f"Generated {len(requests)} flowchart requests for {len(nodes)} nodes and {len(edges)} edges")
    return requests
