"""
SlideMakr - Flowchart Generator

Converts a logical flowchart definition (nodes + edges) into Google Slides API
requests. Supports multiple layout modes: vertical (top-down), horizontal
(left-right), and tree (auto-detect best direction).

The agent calls create_flowchart with a simple structure:
  nodes: [{"id": "start", "label": "Start", "type": "oval"}, ...]
  edges: [{"from": "start", "to": "process1", "label": "Yes"}, ...]
  layout: "vertical" | "horizontal" | "tree" (default: "vertical")

The tool figures out layout, positioning, and generates all the batchUpdate
requests needed. Returns node_object_ids so the agent can further edit shapes.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

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

# Node dimensions (defaults — scaled dynamically per layout)
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

# Colors for different node types (professional light theme)
NODE_COLORS = {
    "ELLIPSE": {"red": 0.23, "green": 0.51, "blue": 0.87},       # Blue for start/end
    "RECTANGLE": {"red": 0.91, "green": 0.93, "blue": 0.96},     # Light gray-blue for process
    "DIAMOND": {"red": 1.0, "green": 0.87, "blue": 0.68},        # Warm peach for decisions
    "ROUND_RECTANGLE": {"red": 0.82, "green": 0.93, "blue": 0.82},  # Light green for subroutines
}

# For start/end ellipses, use white text; for all others, dark text
ELLIPSE_TEXT_COLOR = {"red": 1.0, "green": 1.0, "blue": 1.0}  # White text on blue
TEXT_COLOR = {"red": 0.15, "green": 0.15, "blue": 0.15}  # Dark text
CONNECTOR_COLOR = {"red": 0.35, "green": 0.35, "blue": 0.4}  # Medium gray
LABEL_COLOR = {"red": 0.3, "green": 0.3, "blue": 0.35}  # Dark gray for edge labels


# ============================================================================
# LAYOUT ENGINE
# ============================================================================


def _build_graph(nodes: List[Dict], edges: List[Dict]):
    """Build adjacency data from nodes and edges."""
    node_ids = [n["id"] for n in nodes]
    children = {}
    parents = {}
    for e in edges:
        children.setdefault(e["from"], []).append(e["to"])
        parents.setdefault(e["to"], []).append(e["from"])
    roots = [nid for nid in node_ids if nid not in parents]
    if not roots:
        roots = [node_ids[0]] if node_ids else []
    return node_ids, children, parents, roots


def _bfs_levels(node_ids, children, parents, roots):
    """BFS from roots to assign level numbers."""
    level_assignment = {}
    visited = set()
    queue = [(r, 0) for r in roots]
    for nid, lvl in queue:
        if nid in visited:
            continue
        visited.add(nid)
        level_assignment[nid] = max(level_assignment.get(nid, 0), lvl)
        for child in children.get(nid, []):
            if child not in visited:
                queue.append((child, lvl + 1))
    # Add disconnected nodes
    max_lvl = max(level_assignment.values()) if level_assignment else 0
    for nid in node_ids:
        if nid not in level_assignment:
            max_lvl += 1
            level_assignment[nid] = max_lvl
    # Group by level
    levels = {}
    for nid, lvl in level_assignment.items():
        levels.setdefault(lvl, []).append(nid)
    return levels


def _assign_positions_vertical(nodes: List[Dict], edges: List[Dict]) -> Tuple[Dict, Dict]:
    """Top-down layout: levels = rows, nodes centered horizontally."""
    node_ids, children, parents, roots = _build_graph(nodes, edges)
    levels = _bfs_levels(node_ids, children, parents, roots)

    usable_width = SLIDE_WIDTH - 2 * MARGIN_X
    usable_height = SLIDE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    num_levels = len(levels)
    max_per_level = max(len(v) for v in levels.values()) if levels else 1

    # Dynamic sizing
    eff_w = min(NODE_WIDTH, (usable_width - NODE_H_GAP) // max(max_per_level, 1) - NODE_H_GAP)
    eff_w = max(eff_w, 1200000)
    eff_h = min(NODE_HEIGHT, (usable_height - NODE_V_GAP) // max(num_levels, 1) - NODE_V_GAP)
    eff_h = max(eff_h, 450000)

    positions = {}
    max_bottom = 0
    for lvl_idx in sorted(levels.keys()):
        row_nodes = levels[lvl_idx]
        num_in_row = len(row_nodes)
        total_w = num_in_row * eff_w + (num_in_row - 1) * NODE_H_GAP
        start_x = MARGIN_X + (usable_width - total_w) // 2
        y = MARGIN_TOP + lvl_idx * (eff_h + NODE_V_GAP)
        for col_idx, nid in enumerate(row_nodes):
            x = start_x + col_idx * (eff_w + NODE_H_GAP)
            positions[nid] = {"x": x, "y": y, "w": eff_w, "h": eff_h}
            max_bottom = max(max_bottom, y + eff_h)

    layout_meta = {
        "layout": "vertical",
        "levels_used": num_levels,
        "nodes_per_level": [len(levels[l]) for l in sorted(levels.keys())],
        "max_bottom_emu": max_bottom,
        "max_right_emu": 0,
        "slide_height_emu": SLIDE_HEIGHT,
        "slide_width_emu": SLIDE_WIDTH,
        "fits_slide": max_bottom <= SLIDE_HEIGHT,
        "total_nodes": len(nodes),
    }
    return positions, layout_meta


def _assign_positions_horizontal(nodes: List[Dict], edges: List[Dict]) -> Tuple[Dict, Dict]:
    """Left-to-right layout: levels = columns, nodes centered vertically."""
    node_ids, children, parents, roots = _build_graph(nodes, edges)
    levels = _bfs_levels(node_ids, children, parents, roots)

    usable_width = SLIDE_WIDTH - 2 * MARGIN_X
    usable_height = SLIDE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    num_levels = len(levels)
    max_per_level = max(len(v) for v in levels.values()) if levels else 1

    # For horizontal: width is per-column, height is per-node-in-column
    eff_w = min(NODE_WIDTH, (usable_width - NODE_H_GAP) // max(num_levels, 1) - NODE_H_GAP)
    eff_w = max(eff_w, 1200000)
    eff_h = min(NODE_HEIGHT, (usable_height - NODE_V_GAP) // max(max_per_level, 1) - NODE_V_GAP)
    eff_h = max(eff_h, 450000)

    positions = {}
    max_right = 0
    max_bottom = 0
    for lvl_idx in sorted(levels.keys()):
        col_nodes = levels[lvl_idx]
        num_in_col = len(col_nodes)
        total_h = num_in_col * eff_h + (num_in_col - 1) * NODE_V_GAP
        start_y = MARGIN_TOP + (usable_height - total_h) // 2
        x = MARGIN_X + lvl_idx * (eff_w + NODE_H_GAP)
        for row_idx, nid in enumerate(col_nodes):
            y = start_y + row_idx * (eff_h + NODE_V_GAP)
            positions[nid] = {"x": x, "y": y, "w": eff_w, "h": eff_h}
            max_right = max(max_right, x + eff_w)
            max_bottom = max(max_bottom, y + eff_h)

    layout_meta = {
        "layout": "horizontal",
        "levels_used": num_levels,
        "nodes_per_level": [len(levels[l]) for l in sorted(levels.keys())],
        "max_bottom_emu": max_bottom,
        "max_right_emu": max_right,
        "slide_height_emu": SLIDE_HEIGHT,
        "slide_width_emu": SLIDE_WIDTH,
        "fits_slide": max_right <= SLIDE_WIDTH and max_bottom <= SLIDE_HEIGHT,
        "total_nodes": len(nodes),
    }
    return positions, layout_meta


def _assign_positions_tree(nodes: List[Dict], edges: List[Dict]) -> Tuple[Dict, Dict]:
    """Tree layout: auto-picks vertical or horizontal based on graph shape.

    Wide graphs (many levels, few per level) → horizontal.
    Tall graphs (few levels, many per level) → vertical.
    Decision trees → horizontal (decisions branch vertically).
    """
    node_ids, children, parents, roots = _build_graph(nodes, edges)
    levels = _bfs_levels(node_ids, children, parents, roots)

    num_levels = len(levels)
    max_per_level = max(len(v) for v in levels.values()) if levels else 1

    # Heuristic: if more levels than max width, go horizontal
    if num_levels > max_per_level and num_levels > 3:
        return _assign_positions_horizontal(nodes, edges)
    else:
        return _assign_positions_vertical(nodes, edges)


def _assign_positions(
    nodes: List[Dict],
    edges: List[Dict],
    layout: str = "vertical",
) -> Tuple[Dict, Dict]:
    """Dispatch to the right layout engine.

    Args:
        layout: "vertical" (top-down), "horizontal" (left-right), or "tree" (auto-detect)
    """
    if layout == "horizontal":
        return _assign_positions_horizontal(nodes, edges)
    elif layout == "tree":
        return _assign_positions_tree(nodes, edges)
    else:
        return _assign_positions_vertical(nodes, edges)


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
    layout: str = "vertical",
) -> Tuple[List[Dict[str, Any]], Dict]:
    """Generate Google Slides API requests for a flowchart.

    Args:
        slide_id: The objectId of the slide to draw on
        nodes: List of {"id": str, "label": str, "type": str}
               type is one of: oval/start/end, rectangle/process, diamond/decision, rounded/subroutine
        edges: List of {"from": str, "to": str, "label"?: str}
        title: Optional title text to add at the top
        style: Optional style overrides (bg_color, text_color, etc.)
        layout: "vertical" (top-down), "horizontal" (left-right), or "tree" (auto-detect)

    Returns:
        Tuple of (requests list, layout_meta dict)
    """
    requests = []
    positions, layout_meta = _assign_positions(nodes, edges, layout=layout)
    node_map = {n["id"]: n for n in nodes}

    # Custom colors from style (no forced background — inherits from template)
    txt_color = (style or {}).get("text_color", TEXT_COLOR)
    conn_color = (style or {}).get("connector_color", CONNECTOR_COLOR)

    # 1. Optional title
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

        # Shape fill + outline
        outline_color = {"red": 0.7, "green": 0.7, "blue": 0.75} if shape_type != "ELLIPSE" else conn_color
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
                                "color": {"rgbColor": outline_color},
                                "alpha": 0.8,
                            }
                        },
                        "weight": {"magnitude": 1.5, "unit": "PT"},
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

        # Style text — white on blue ellipses, dark on everything else
        node_txt_color = ELLIPSE_TEXT_COLOR if shape_type == "ELLIPSE" else txt_color
        font_size = 12 if len(label) > 20 else 14
        requests.append({
            "updateTextStyle": {
                "objectId": shape_id,
                "style": {
                    "foregroundColor": {"opaqueColor": {"rgbColor": node_txt_color}},
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
    is_horizontal = layout_meta.get("layout") == "horizontal"

    for edge in edges:
        from_id = edge.get("from")
        to_id = edge.get("to")
        from_pos = positions.get(from_id)
        to_pos = positions.get(to_id)
        if not from_pos or not to_pos:
            continue

        line_id = _uid("edge")

        if is_horizontal:
            # Horizontal layout: primary flow is left→right
            # Default: right side of from → left side of to
            from_cx = from_pos["x"] + from_pos["w"]
            from_by = from_pos["y"] + from_pos["h"] // 2
            to_cx = to_pos["x"]
            to_ty = to_pos["y"] + to_pos["h"] // 2

            # If nodes are in the same column, connect bottom-to-top
            if abs(from_pos["x"] - to_pos["x"]) < NODE_H_GAP // 2:
                from_cx = from_pos["x"] + from_pos["w"] // 2
                from_by = from_pos["y"] + from_pos["h"]
                to_cx = to_pos["x"] + to_pos["w"] // 2
                to_ty = to_pos["y"]
                if from_pos["y"] > to_pos["y"]:
                    from_by = from_pos["y"]
                    to_ty = to_pos["y"] + to_pos["h"]
            # If target is to the left, reverse direction
            elif to_pos["x"] + to_pos["w"] <= from_pos["x"]:
                from_cx = from_pos["x"]
                to_cx = to_pos["x"] + to_pos["w"]
        else:
            # Vertical layout: primary flow is top→bottom
            # Default: center-bottom of source → center-top of target
            from_cx = from_pos["x"] + from_pos["w"] // 2
            from_by = from_pos["y"] + from_pos["h"]
            to_cx = to_pos["x"] + to_pos["w"] // 2
            to_ty = to_pos["y"]

            # If nodes are on the same row, connect side-to-side
            if abs(from_pos["y"] - to_pos["y"]) < NODE_V_GAP // 2:
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

    # Include node objectIds in metadata so the agent can edit shapes later
    layout_meta["node_object_ids"] = node_object_ids

    logger.info(f"Generated {len(requests)} flowchart requests for {len(nodes)} nodes, {len(edges)} edges, layout={layout_meta.get('layout', 'vertical')}")
    return requests, layout_meta
