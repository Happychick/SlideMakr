"""
Tests for app/layout_quality.py — deterministic layout + color scoring used to
ground the adherence/visual metric in real slide geometry, not just presence.

All inputs are the element dicts that get_presentation_state already returns
(objectId, type, size{width/height.magnitude}, transform{translateX/Y, scaleX/Y},
plus fill_color we add). Pure functions, no API.
"""

from __future__ import annotations

from app import layout_quality as lq


# Slide bounds (EMU): 10" x 5.625"
SLIDE_W = 9_144_000
SLIDE_H = 5_143_500


def _el(oid, x, y, w, h, fill=None):
    el = {
        "objectId": oid,
        "type": "shape",
        "size": {"width": {"magnitude": w}, "height": {"magnitude": h}},
        "transform": {"translateX": x, "translateY": y, "scaleX": 1, "scaleY": 1},
    }
    if fill:
        el["fill_color"] = fill
    return el


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------

def test_vertical_flowchart_detected():
    # Three nodes stacked top-to-bottom at the same X.
    nodes = [
        _el("node_a", 4_000_000, 200_000, 1_000_000, 600_000),
        _el("node_b", 4_000_000, 1_400_000, 1_000_000, 600_000),
        _el("node_c", 4_000_000, 2_600_000, 1_000_000, 600_000),
    ]
    assert lq.flowchart_orientation(nodes) == "vertical"


def test_horizontal_flowchart_detected():
    nodes = [
        _el("node_a", 200_000, 2_000_000, 1_000_000, 600_000),
        _el("node_b", 1_800_000, 2_000_000, 1_000_000, 600_000),
        _el("node_c", 3_400_000, 2_000_000, 1_000_000, 600_000),
    ]
    assert lq.flowchart_orientation(nodes) == "horizontal"


# ---------------------------------------------------------------------------
# Fits the page
# ---------------------------------------------------------------------------

def test_elements_within_bounds_score_full():
    els = [_el("a", 500_000, 500_000, 1_000_000, 1_000_000)]
    assert lq.fits_page_score(els, SLIDE_W, SLIDE_H) == 1.0


def test_element_off_slide_penalised():
    # Element runs off the right + bottom edge.
    els = [_el("a", 8_800_000, 5_000_000, 1_500_000, 1_000_000)]
    assert lq.fits_page_score(els, SLIDE_W, SLIDE_H) < 1.0


# ---------------------------------------------------------------------------
# Overlap
# ---------------------------------------------------------------------------

def test_no_overlap_scores_full():
    els = [
        _el("a", 0, 0, 1_000_000, 1_000_000),
        _el("b", 2_000_000, 0, 1_000_000, 1_000_000),
    ]
    assert lq.overlap_score(els) == 1.0


def test_overlapping_elements_penalised():
    els = [
        _el("a", 0, 0, 2_000_000, 2_000_000),
        _el("b", 500_000, 500_000, 2_000_000, 2_000_000),
    ]
    assert lq.overlap_score(els) < 1.0


# ---------------------------------------------------------------------------
# Colour coherence
# ---------------------------------------------------------------------------

def test_coherent_palette_scores_high():
    els = [
        _el("a", 0, 0, 1, 1, fill="#6B46C1"),
        _el("b", 0, 0, 1, 1, fill="#6B46C1"),
        _el("c", 0, 0, 1, 1, fill="#FFFFFF"),
    ]
    assert lq.color_coherence_score(els) >= 0.8


def test_rainbow_palette_scores_low():
    els = [
        _el("a", 0, 0, 1, 1, fill="#FF0000"),
        _el("b", 0, 0, 1, 1, fill="#00FF00"),
        _el("c", 0, 0, 1, 1, fill="#0000FF"),
        _el("d", 0, 0, 1, 1, fill="#FFFF00"),
        _el("e", 0, 0, 1, 1, fill="#FF00FF"),
        _el("f", 0, 0, 1, 1, fill="#00FFFF"),
    ]
    assert lq.color_coherence_score(els) < 0.6


# ---------------------------------------------------------------------------
# Deck-level composite (feeds score_visual_quality)
# ---------------------------------------------------------------------------

def test_clean_deck_scores_high():
    state = {
        "slides": [
            {"elements": [
                _el("a", 500_000, 500_000, 1_000_000, 800_000, fill="#6B46C1"),
                _el("b", 500_000, 1_600_000, 1_000_000, 800_000, fill="#6B46C1"),
            ]},
        ]
    }
    assert lq.score_layout_from_state(state) >= 0.9


def test_messy_deck_scores_low():
    state = {
        "slides": [
            {"elements": [
                # off-slide + overlapping + rainbow
                _el("a", 8_900_000, 4_900_000, 2_000_000, 1_500_000, fill="#FF0000"),
                _el("b", 8_950_000, 4_950_000, 2_000_000, 1_500_000, fill="#00FF00"),
                _el("c", 0, 0, 1, 1, fill="#0000FF"),
                _el("d", 0, 0, 1, 1, fill="#FFFF00"),
                _el("e", 0, 0, 1, 1, fill="#FF00FF"),
            ]},
        ]
    }
    assert lq.score_layout_from_state(state) < 0.6


def test_empty_deck_is_neutral():
    assert lq.score_layout_from_state({"slides": []}) == 0.5
