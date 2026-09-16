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


# A hard penalty applies once ANY element — text-bearing or not — covers more
# than this fraction of a text-bearing element's own area. A deck-average
# score can hide one badly-placed element, and anything sitting over text by
# more than a sliver is a real legibility failure, not a nuance. Two ordinary
# text boxes clipping each other by a hair (a template's title/body corner)
# stay proportional; past the threshold they cap too.
_IMAGE_OVER_TEXT_HARD_THRESHOLD = 0.10
_IMAGE_OVER_TEXT_SCORE_CAP = 0.3


def _has_text(el: Dict[str, Any]) -> bool:
    return bool(str(el.get("text", "")).strip())


def _excluded_pair(ei: Dict[str, Any], ej: Dict[str, Any]) -> bool:
    """True for pairs exempt from the cap: flowchart-internal geometry.

    An edge line under its own node, or two flowchart nodes overlapping each
    other, are expected layout — not a legibility failure.
    """
    oid_i = str(ei.get("objectId", ""))
    oid_j = str(ej.get("objectId", ""))
    if oid_i.startswith("edge_") or oid_j.startswith("edge_"):
        return True
    if oid_i.startswith("node_") and oid_j.startswith("node_"):
        return True
    return False


def _cap_candidate(
    ei: Dict[str, Any], ej: Dict[str, Any], ix: float, iy: float
) -> Optional[Tuple[bool, Dict[str, Any], Dict[str, Any]]]:
    """(is_capped, blocker, text_el) for the pair, or None if neither side has text.

    `text_el` is whichever text-bearing side the overlap covers the larger
    fraction of — that's the more severe reading when both sides have text
    (e.g. two overlapping text boxes, or a labeled flowchart node over body
    text). Exempt pairs (see `_excluded_pair`) never produce a candidate.
    """
    if _excluded_pair(ei, ej):
        return None
    best: Optional[Tuple[float, Dict[str, Any], Dict[str, Any]]] = None
    for victim, blocker in ((ei, ej), (ej, ei)):
        if not _has_text(victim):
            continue
        victim_box = _box(victim)
        if not victim_box:
            continue
        _, _, vw, vh = victim_box
        victim_area = vw * vh
        if victim_area <= 0:
            continue
        ratio = (ix * iy) / victim_area
        if best is None or ratio > best[0]:
            best = (ratio, blocker, victim)
    if best is None:
        return None
    ratio, blocker, victim = best
    return ratio > _IMAGE_OVER_TEXT_HARD_THRESHOLD, blocker, victim


def _overlapping_pairs(
    elements: List[Dict[str, Any]],
) -> List[Tuple[Dict[str, Any], Dict[str, Any], float, float]]:
    """Every pair of elements whose boxes overlap: (el_a, el_b, overlap_w, overlap_h)."""
    paired = [(e, b) for e in elements if (b := _box(e))]
    pairs = []
    for i in range(len(paired)):
        ei, (xi, yi, wi, hi) = paired[i]
        for j in range(i + 1, len(paired)):
            ej, (xj, yj, wj, hj) = paired[j]
            ix = max(0.0, min(xi + wi, xj + wj) - max(xi, xj))
            iy = max(0.0, min(yi + hi, yj + hj) - max(yi, yj))
            if ix > 0 and iy > 0:
                pairs.append((ei, ej, ix, iy))
    return pairs


def overlap_details(elements: List[Dict[str, Any]]) -> List[str]:
    """Human-readable line per overlapping pair, naming objectIds.

    Flags a pair that overlaps a text-bearing element explicitly
    ("<kind> over text") since that's the legibility failure `overlap_score`
    hard-penalizes.
    """
    details = []
    for ei, ej, ix, iy in _overlapping_pairs(elements):
        oid_i = ei.get("objectId", "?")
        oid_j = ej.get("objectId", "?")
        candidate = _cap_candidate(ei, ej, ix, iy)
        if candidate is not None:
            _is_capped, blocker, txt = candidate
            kind = blocker.get("type") or "shape"
            if kind == "image":
                # Preserve the established phrasing — other call sites
                # (e.g. edit_flow_eval's usability reason) match on it.
                details.append(
                    f"{blocker.get('objectId', '?')} covers "
                    f"{txt.get('objectId', '?')} (image over text)"
                )
            else:
                details.append(
                    f"{kind} {blocker.get('objectId', '?')} over text "
                    f"{txt.get('objectId', '?')}"
                )
        else:
            details.append(f"{oid_i} overlaps {oid_j}")
    return details


def overlap_score(elements: List[Dict[str, Any]]) -> float:
    """1.0 when no elements overlap; degrades with overlapping area.

    Score = 1 - (total pairwise overlap area / total element area), clamped.
    Hard-capped at `_IMAGE_OVER_TEXT_SCORE_CAP` when ANY element — text-bearing
    or not — covers more than `_IMAGE_OVER_TEXT_HARD_THRESHOLD` of a
    text-bearing element's own area — that failure must not be averaged away
    by an otherwise-tidy slide. Exempt: an edge line under its own node, and
    two flowchart nodes overlapping each other (`_excluded_pair`).
    """
    boxes = [b for b in (_box(e) for e in elements) if b]
    if len(boxes) < 2:
        return 1.0
    total_area = sum(w * h for _, _, w, h in boxes) or 1.0
    overlap = 0.0
    hard_penalty = False
    for ei, ej, ix, iy in _overlapping_pairs(elements):
        overlap += ix * iy
        candidate = _cap_candidate(ei, ej, ix, iy)
        if candidate is not None and candidate[0]:
            hard_penalty = True
    score = round(max(0.0, 1.0 - overlap / total_area), 4)
    return min(score, _IMAGE_OVER_TEXT_SCORE_CAP) if hard_penalty else score


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


def _hex_to_rgb(value: str) -> Optional[Tuple[int, int, int]]:
    v = str(value).strip().lstrip("#")
    if len(v) != 6:
        return None
    try:
        return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    except ValueError:
        return None


def _is_neutral(value: str) -> bool:
    """White / black / grey — free to use, not counted as a brand colour."""
    rgb = _hex_to_rgb(value)
    if rgb is None:
        return True  # non-hex (theme token) — ignore in brand matching
    return max(rgb) - min(rgb) < 25


def _rgb_distance(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def extract_hex_colors(text: str) -> List[str]:
    """Pull '#RRGGBB' codes out of a branding-summary blob, in order, deduped."""
    import re
    seen: List[str] = []
    for m in re.findall(r"#[0-9A-Fa-f]{6}", text or ""):
        h = m.upper()
        if h not in seen:
            seen.append(h)
    return seen


def brand_match_score(
    deck_colors: List[str],
    brand_colors: List[str],
    tolerance: float = 60.0,
) -> float:
    """Did the deck actually use the brand palette? (0-1)

    Neutrals (white/black/grey) are free and ignored. Of the deck's chromatic
    fills, the score is the fraction that land within `tolerance` of some brand
    colour. A deck with no colours, or only neutrals, scores 0 — branding was
    requested but never applied (the exact default-template failure mode).
    """
    if not brand_colors:
        return 1.0  # nothing to match against
    brand_rgb = [c for c in (_hex_to_rgb(b) for b in brand_colors) if c]
    if not brand_rgb:
        return 1.0
    chromatic = [c for c in deck_colors if not _is_neutral(c)]
    if not chromatic:
        return 0.0
    matched = 0
    for c in chromatic:
        rgb = _hex_to_rgb(c)
        if rgb and min(_rgb_distance(rgb, b) for b in brand_rgb) <= tolerance:
            matched += 1
    return round(matched / len(chromatic), 4)


def font_consistency_score(state: Dict[str, Any]) -> float:
    """Fraction of text elements using the deck's dominant font (1.0 = uniform).

    A flowchart or added element in a different typeface drags this down — the
    agent should build in the deck's font, and this catches it deterministically.
    """
    fonts = [
        e["font"]
        for s in (state or {}).get("slides", [])
        for e in s.get("elements", [])
        if e.get("font")
    ]
    if not fonts:
        return 1.0
    from collections import Counter
    dominant = Counter(fonts).most_common(1)[0][1]
    return round(dominant / len(fonts), 4)


def balance_score(
    elements: List[Dict[str, Any]],
    slide_w: int = SLIDE_W_EMU,
    slide_h: int = SLIDE_H_EMU,
) -> float:
    """How centered the slide's content is (1.0 = content centroid at slide center).

    Catches lopsided layouts (e.g. a flowchart bunched on one side with dead
    space) without a vision model.
    """
    boxes = [b for b in (_box(e) for e in elements) if b]
    if not boxes:
        return 1.0
    min_x = min(x for x, _, _, _ in boxes)
    max_x = max(x + w for x, _, w, _ in boxes)
    min_y = min(y for _, y, _, _ in boxes)
    max_y = max(y + h for _, y, _, h in boxes)
    cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2
    dx = abs(cx - slide_w / 2) / (slide_w / 2)
    dy = abs(cy - slide_h / 2) / (slide_h / 2)
    return round(max(0.0, 1.0 - (dx + dy) / 2), 4)


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
