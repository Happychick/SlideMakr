"""
Tests for app/quality_rules.py: the registry both the agent prompt and the
eval consume for "looks good".
"""

from __future__ import annotations

from app import edit_assess as ea
from app import quality_rules as qr


_SLIDE_W, _SLIDE_H = 9_144_000, 5_143_500


def _centered_box(x_w: int, x_h: int, object_id: str, **extra):
    x = (_SLIDE_W - x_w) // 2
    y = (_SLIDE_H - x_h) // 2
    return {
        "objectId": object_id,
        "size": {"width": {"magnitude": x_w}, "height": {"magnitude": x_h}},
        "transform": {"translateX": x, "translateY": y, "scaleX": 1, "scaleY": 1},
        **extra,
    }


def _overlap_state():
    """Same fixture as test_edit_assess: overlap=0.3, everything else 1.0."""
    w, h = 4_000_000, 2_000_000
    return {
        "slide_count": 1,
        "slides": [{
            "slide_id": "s1", "slide_index": 0,
            "elements": [
                _centered_box(w, h, "title_1", type="shape", text="Title",
                               placeholder="TITLE"),
                _centered_box(w, h, "image_1", type="image"),
            ],
        }],
    }


def _perfect_state():
    return {
        "slide_count": 1,
        "slides": [{
            "slide_id": "s1", "slide_index": 0,
            "elements": [
                {"objectId": "title_1", "type": "shape", "text": "Title",
                 "placeholder": "TITLE",
                 "size": {"width": {"magnitude": 1}, "height": {"magnitude": 1}},
                 "transform": {"translateX": _SLIDE_W // 2, "translateY": _SLIDE_H // 2,
                               "scaleX": 1, "scaleY": 1}},
            ],
        }],
    }


def _no_title_state():
    return {
        "slide_count": 1,
        "slides": [{
            "slide_id": "s1", "slide_index": 0,
            "elements": [
                {"objectId": "body_1", "type": "shape", "text": "Some body text",
                 "size": {"width": {"magnitude": 1}, "height": {"magnitude": 1}},
                 "transform": {"translateX": _SLIDE_W // 2, "translateY": 3_000_000,
                               "scaleX": 1, "scaleY": 1}},
            ],
        }],
    }


# ---------------------------------------------------------------------------
# score() reproduces today's usability_parts
# ---------------------------------------------------------------------------

def test_score_matches_usability_parts_on_overlap_fixture():
    state = _overlap_state()
    rs = qr.RuleSet(qr.DEFAULT_RULES)
    scored = rs.score(state, 0)["universal"]
    parts = ea.usability_parts(state, 0)
    assert scored["no_overlap"] == parts["overlap"]
    assert scored["fits_page"] == parts["fit"]
    assert scored["one_font"] == parts["font"]
    assert scored["balanced"] == parts["balance"]


def test_score_matches_usability_parts_on_perfect_fixture():
    state = _perfect_state()
    rs = qr.RuleSet(qr.DEFAULT_RULES)
    scored = rs.score(state, 0)["universal"]
    parts = ea.usability_parts(state, 0)
    assert scored["no_overlap"] == parts["overlap"] == 1.0
    assert scored["fits_page"] == parts["fit"] == 1.0
    assert scored["one_font"] == parts["font"] == 1.0
    assert scored["balanced"] == parts["balance"] == 1.0


def test_score_excludes_prompt_only_rules():
    rs = qr.RuleSet(qr.DEFAULT_RULES)
    scored = rs.score(_perfect_state(), 0)["universal"]
    assert "charts_titled" not in scored  # check_id is None


# ---------------------------------------------------------------------------
# Fix round 1 (Task 1b.4 review): has_title is back in DEFAULT_RULES (soft),
# so looks_good_baseline reproduces the old usability product on a titleless
# slide instead of silently dropping the title penalty.
# ---------------------------------------------------------------------------

def test_default_rules_has_title_is_soft_not_hard():
    rule = next(r for r in qr.DEFAULT_RULES if r.id == "has_title")
    assert rule.check_id == "has_title"
    assert rule.scope == "universal"
    assert rule.hard is False


def test_looks_good_baseline_matches_old_usability_on_titleless_slide():
    state = _no_title_state()
    rs = qr.RuleSet(qr.DEFAULT_RULES)
    baseline, _user = rs.looks_good(state, 0)
    assert baseline == ea.usability(state, 0)
    assert baseline < 1.0  # the missing-title penalty is actually applied


def test_charts_titled_stays_prompt_only():
    rs = qr.RuleSet(qr.DEFAULT_RULES)
    scored = rs.score(_perfect_state(), 0)["universal"]
    assert "charts_titled" not in scored
    assert "has_title" in scored


def test_edit_assess_usability_parts_still_has_same_keys_and_values():
    # Existing tests (test_edit_assess.py) stay green: same keys/semantics.
    state = _overlap_state()
    parts = ea.usability_parts(state, 0)
    assert set(parts.keys()) == {"font", "balance", "fit", "overlap", "title"}
    assert parts["overlap"] == 0.3
    assert ea.usability(state, 0) == 0.3


# ---------------------------------------------------------------------------
# render_for_prompt
# ---------------------------------------------------------------------------

def test_render_for_prompt_lists_universal_rule_texts():
    rs = qr.RuleSet(qr.DEFAULT_RULES)
    rendered = rs.render_for_prompt()
    assert "Quality rules -- you are scored on exactly these" in rendered
    for r in qr.DEFAULT_RULES:
        assert r.text in rendered


def test_render_for_prompt_puts_user_rules_under_preferences_heading():
    user_rule = qr.Rule(
        id="user:headings_green", text="Headings should be green.",
        scope="user", check_id="text_color", params={"role": "headings", "hex": "#008000"},
    )
    rs = qr.RuleSet(qr.DEFAULT_RULES + [user_rule])
    rendered = rs.render_for_prompt()
    assert "### This user's preferences" in rendered
    pref_idx = rendered.index("### This user's preferences")
    rule_idx = rendered.index(user_rule.text)
    assert rule_idx > pref_idx


def test_render_for_prompt_no_preferences_section_when_no_user_rules():
    rs = qr.RuleSet(qr.DEFAULT_RULES)
    rendered = rs.render_for_prompt()
    assert "This user's preferences" not in rendered


# ---------------------------------------------------------------------------
# from_dicts / to_dicts round-trip
# ---------------------------------------------------------------------------

def test_from_dicts_to_dicts_round_trip():
    rs = qr.RuleSet(qr.DEFAULT_RULES)
    dicts = rs.to_dicts()
    rs2 = qr.RuleSet.from_dicts(dicts)
    assert rs2.to_dicts() == dicts


def test_from_dicts_builds_user_rule_with_params():
    d = {
        "id": "user:numbers_bold", "text": "Numbers bold.", "scope": "user",
        "check_id": "text_style", "params": {"bold": True, "target": "numbers"},
        "hard": False, "source": "feedback:2026-09-21T00:00:00Z",
    }
    rs = qr.RuleSet.from_dicts([d])
    (rule,) = rs.rules
    assert rule.check_id == "text_style"
    assert rule.params == {"bold": True, "target": "numbers"}
    assert rule.source == "feedback:2026-09-21T00:00:00Z"


# ---------------------------------------------------------------------------
# New checks
# ---------------------------------------------------------------------------

def test_image_present_true_when_image_element_exists():
    state = {"slide_count": 1, "slides": [{"slide_id": "s1", "slide_index": 0,
             "elements": [{"objectId": "i1", "type": "image"}]}]}
    assert qr.CHECKS["image_present"](state, 0, {}) == 1.0


def test_image_present_false_when_no_image():
    state = {"slide_count": 1, "slides": [{"slide_id": "s1", "slide_index": 0,
             "elements": [{"objectId": "t1", "type": "shape", "text": "hi"}]}]}
    assert qr.CHECKS["image_present"](state, 0, {}) == 0.0


def test_image_present_exempts_slide_zero_with_only_content_slides():
    state = {"slide_count": 1, "slides": [{"slide_id": "s1", "slide_index": 0,
             "elements": []}]}
    assert qr.CHECKS["image_present"](state, 0, {"only_content_slides": True}) == 1.0
    assert qr.CHECKS["image_present"](state, 1, {"only_content_slides": True}) == 0.0


def test_text_color_headings_half_match():
    state = {"slide_count": 1, "slides": [{"slide_id": "s1", "slide_index": 0, "elements": [
        {"objectId": "h1", "placeholder": "TITLE", "text": "One", "text_color": "#008000"},
        {"objectId": "h2", "placeholder": "TITLE", "text": "Two", "text_color": "#0B2A6F"},
    ]}]}
    score = qr.CHECKS["text_color"](state, 0, {"role": "headings", "hex": "#008000"})
    assert score == 0.5


def test_text_color_case_insensitive_match():
    state = {"slide_count": 1, "slides": [{"slide_id": "s1", "slide_index": 0, "elements": [
        {"objectId": "h1", "placeholder": "TITLE", "text": "One", "text_color": "#AbCdEf"},
    ]}]}
    score = qr.CHECKS["text_color"](state, 0, {"role": "headings", "hex": "#abcdef"})
    assert score == 1.0


def test_text_color_no_elements_in_role_is_one():
    state = {"slide_count": 1, "slides": [{"slide_id": "s1", "slide_index": 0, "elements": []}]}
    assert qr.CHECKS["text_color"](state, 0, {"role": "headings", "hex": "#008000"}) == 1.0


def test_text_style_no_bold_info_returns_one():
    state = {"slide_count": 1, "slides": [{"slide_id": "s1", "slide_index": 0, "elements": [
        {"objectId": "b1", "text": "Revenue grew 42%"},
    ]}]}
    assert qr.CHECKS["text_style"](state, 0, {"bold": True, "target": "numbers"}) == 1.0


def test_text_style_scores_bold_fraction_when_state_has_bold_flags():
    state = {"slide_count": 1, "slides": [{"slide_id": "s1", "slide_index": 0, "elements": [
        {"objectId": "b1", "text": "42% growth", "bold": True},
        {"objectId": "b2", "text": "13 clients", "bold": False},
    ]}]}
    score = qr.CHECKS["text_style"](state, 0, {"bold": True, "target": "numbers"})
    assert score == 0.5


def test_brand_match_delegates_to_layout_quality():
    state = {"slide_count": 1, "slides": [{"slide_id": "s1", "slide_index": 0, "elements": [
        {"objectId": "s1", "fill_color": "#635BFF"},
        {"objectId": "s2", "fill_color": "#FF0000"},
    ]}]}
    score = qr.CHECKS["brand_match"](state, 0, {"colors": ["#635BFF"]})
    assert score == 0.5


# ---------------------------------------------------------------------------
# looks_good
# ---------------------------------------------------------------------------

def test_looks_good_baseline_and_user_are_products():
    state = _overlap_state()
    rs = qr.RuleSet(qr.DEFAULT_RULES)
    baseline, user = rs.looks_good(state, 0)
    assert baseline == 0.3  # no_overlap=0.3, everything else 1.0
    assert user == 1.0      # no user rules -> 1.0


def test_looks_good_user_bucket_is_product_of_user_rules():
    user_rule = qr.Rule(
        id="user:image_present", text="Every content slide has an image.",
        scope="user", check_id="image_present", params={}, hard=True,
    )
    state = {"slide_count": 1, "slides": [{"slide_id": "s1", "slide_index": 0,
             "elements": [{"objectId": "t1", "type": "shape", "text": "hi",
                           "placeholder": "TITLE"}]}]}
    rs = qr.RuleSet(qr.DEFAULT_RULES + [user_rule])
    _, user = rs.looks_good(state, 0)
    assert user == 0.0  # no image present


# ---------------------------------------------------------------------------
# hard_failures
# ---------------------------------------------------------------------------

def test_hard_failures_returns_no_overlap_on_node_over_text_fixture():
    state = _overlap_state()  # image fully covering the title text box
    rs = qr.RuleSet(qr.DEFAULT_RULES)
    failures = rs.hard_failures(state, 0)
    ids = {r.id for r in failures}
    assert "no_overlap" in ids
    assert "fits_page" not in ids
    assert "one_font" not in ids


def test_hard_failures_empty_on_perfect_slide():
    rs = qr.RuleSet(qr.DEFAULT_RULES)
    assert rs.hard_failures(_perfect_state(), 0) == []


# ---------------------------------------------------------------------------
# Unknown check_id handling (review fix round 1)
# ---------------------------------------------------------------------------

import logging
import pytest


def test_rule_from_dict_rejects_unknown_check_id():
    d = {"id": "user:bogus", "text": "bogus", "scope": "user", "check_id": "nope",
         "params": {}, "hard": False}
    with pytest.raises(ValueError, match="unknown check_id"):
        qr.Rule.from_dict(d)


def test_rule_construction_rejects_unknown_check_id_directly():
    with pytest.raises(ValueError, match="unknown check_id"):
        qr.Rule(id="x", text="x", scope="user", check_id="nope")


def test_ruleset_from_dicts_strict_raises_on_unknown_check_id():
    d = {"id": "user:bogus", "text": "bogus", "scope": "user", "check_id": "nope",
         "params": {}, "hard": False}
    with pytest.raises(ValueError, match="unknown check_id"):
        qr.RuleSet.from_dicts([d], strict=True)


def test_ruleset_from_dicts_nonstrict_drops_and_logs(caplog):
    good = {"id": "no_overlap", "text": "no overlap", "scope": "universal",
            "check_id": "overlap", "params": {}, "hard": True}
    bad = {"id": "user:bogus", "text": "bogus", "scope": "user", "check_id": "nope",
           "params": {}, "hard": False}
    with caplog.at_level(logging.WARNING):
        rs = qr.RuleSet.from_dicts([good, bad], strict=False)
    assert [r.id for r in rs.rules] == ["no_overlap"]
    assert any("user:bogus" in rec.message for rec in caplog.records)
    # The rest still scores fine.
    scored = rs.score(_perfect_state(), 0)["universal"]
    assert scored["no_overlap"] == 1.0


# ---------------------------------------------------------------------------
# is_heading_element shared with edit_assess.check_has_title (review fix)
# ---------------------------------------------------------------------------

def test_text_color_skips_flowchart_node_labels_near_top():
    state = {"slide_count": 1, "slides": [{"slide_id": "s1", "slide_index": 0, "elements": [
        {"objectId": "node_1", "text": "Plan", "text_color": "#FF0000",
         "transform": {"translateY": 100_000}},
        {"objectId": "h1", "placeholder": "TITLE", "text": "Title",
         "text_color": "#008000"},
    ]}]}
    score = qr.CHECKS["text_color"](state, 0, {"role": "headings", "hex": "#008000"})
    # Only h1 counts as a heading -> full match.
    assert score == 1.0
