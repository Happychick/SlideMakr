"""
SlideMakr — deterministic layout & colour scoring for the adherence metric.

Grounds "accurate" in real slide geometry instead of mere element presence:
given the elements get_presentation_state returns (objectId, size, transform,
fill_color), score whether things fit the page, don't overlap, a flowchart is
oriented as asked, and the palette is coherent. No vision model, no network —
pure functions so the metric is reliable and unit-testable.

EMU coordinate system: an element's on-slide box is
    x = transform.translateX,  y = transform.translateY
    w = size.width.magnitude  * transform.scaleX
    h = size.height.magnitude * transform.scaleY
Slide default: 9_144_000 x 5_143_500 EMU.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

SLIDE_W_EMU = 9_144_000
SLIDE_H_EMU = 5_143_500


def _box(el: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    """Return (x, y, w, h) in EMU for an element, or None if geometry missing."""
    size = el.get("size") or {}
    tf = el.get("transform") or {}
    try:
        w = size["width"]["magnitude"] * tf.get("scaleX", 1)
        h = size["height"]["magnitude"] * tf.get("scaleY", 1)
        x = tf["translateX"]
        y = tf["translateY"]
    except (KeyError, TypeError):
        return None
    return float(x), float(y), float(w), float(h)


def _nodes(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flowchart node shapes — identified by the engine's `node_` objectId prefix."""
    return [e for e in elements if str(e.get("objectId", "")).startswith("node_")]


def flowchart_orientation(elements: List[Dict[str, Any]]) -> str:
    """'vertical', 'horizontal', or 'none' based on how the nodes are spread.

    Vertical = nodes vary more in Y than X (stacked); horizontal = the reverse.
    """
    boxes = [b for b in (_box(e) for e in _nodes(elements)) if b]
    if len(boxes) < 2:
        return "none"
    xs = [x for x, _, _, _ in boxes]
    ys = [y for _, y, _, _ in boxes]
    x_spread = max(xs) - min(xs)
    y_spread = max(ys) - min(ys)
    if y_spread >= x_spread:
        return "vertical"
    return "horizontal"


def fits_page_score(
    elements: List[Dict[str, Any]],
    slide_w: int = SLIDE_W_EMU,
    slide_h: int = SLIDE_H_EMU,
) -> float:
    """1.0 if every element sits fully on-slide; degrades with off-slide area.

    Score = average per-element fraction of the element's box that lies inside
    the slide rectangle. An element fully off-slide contributes 0.
    """
    boxes = [b for b in (_box(e) for e in elements) if b]
    if not boxes:
        return 1.0
    fractions = []
    for x, y, w, h in boxes:
        area = w * h
        if area <= 0:
            fractions.append(1.0)
            continue
        ix = max(0.0, min(x + w, slide_w) - max(x, 0.0))
        iy = max(0.0, min(y + h, slide_h) - max(y, 0.0))
        fractions.append((ix * iy) / area)
    return round(sum(fractions) / len(fractions), 4)


def overlap_score(elements: List[Dict[str, Any]]) -> float:
    """1.0 when no elements overlap; degrades with overlapping area.

    Score = 1 - (total pairwise overlap area / total element area), clamped.
    """
    boxes = [b for b in (_box(e) for e in elements) if b]
    if len(boxes) < 2:
        return 1.0
    total_area = sum(w * h for _, _, w, h in boxes) or 1.0
    overlap = 0.0
    for i in range(len(boxes)):
        xi, yi, wi, hi = boxes[i]
        for j in range(i + 1, len(boxes)):
            xj, yj, wj, hj = boxes[j]
            ix = max(0.0, min(xi + wi, xj + wj) - max(xi, xj))
            iy = max(0.0, min(yi + hi, yj + hj) - max(yi, yj))
            overlap += ix * iy
    return round(max(0.0, 1.0 - overlap / total_area), 4)


def color_coherence_score(elements: List[Dict[str, Any]]) -> float:
    """Reward a small, consistent palette; penalise a rainbow of fills.

    Looks only at elements that declare a fill_color. 1-2 distinct colours = 1.0;
    each additional distinct colour reduces the score.
    """
    colors = [
        str(e["fill_color"]).upper()
        for e in elements
        if e.get("fill_color")
    ]
    if not colors:
        return 1.0  # nothing to judge
    distinct = len(set(colors))
    if distinct <= 2:
        return 1.0
    # 3 colours → 0.8, 4 → 0.6, 5 → 0.4 … floor at 0.0
    return round(max(0.0, 1.0 - (distinct - 2) * 0.2), 4)


def score_layout_from_state(
    state: Dict[str, Any],
    slide_w: int = SLIDE_W_EMU,
    slide_h: int = SLIDE_H_EMU,
) -> float:
    """Deck-level layout/colour quality (0-1) — powers score_visual_quality.

    Per slide = mean(fits_page, no_overlap, colour_coherence); deck = mean of
    slides. An empty deck returns a neutral 0.5 (nothing to judge).
    """
    slides = state.get("slides", []) if state else []
    if not slides:
        return 0.5
    per_slide = []
    for slide in slides:
        els = slide.get("elements", [])
        per_slide.append((
            fits_page_score(els, slide_w, slide_h)
            + overlap_score(els)
            + color_coherence_score(els)
        ) / 3.0)
    return round(sum(per_slide) / len(per_slide), 4)
