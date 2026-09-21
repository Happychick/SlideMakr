"""
Tests for app/edit_flow_eval: one-deck edit-flow eval with a hard deck budget.

One run = one deck created, then a fixed script of six text edits applied to
THAT SAME deck, each scored. No network — create_fn/edit_fn are fakes and
app.slidemakr.get_presentation_state is monkeypatched. Uses asyncio.run (like
tests/test_eval_adherence.py) rather than pytest-asyncio, which isn't a
project dependency.
"""

from __future__ import annotations

import asyncio

import pytest

from app import edit_flow_eval as efe


# ---------------------------------------------------------------------------
# EDIT_FLOW script shape
# ---------------------------------------------------------------------------

def test_edit_flow_has_create_prompt_and_six_edits():
    assert isinstance(efe.EDIT_FLOW["create"], str) and efe.EDIT_FLOW["create"]
    edits = efe.EDIT_FLOW["edits"]
    assert len(edits) == 6
    assert edits[0] == "change the title of slide 1 to Remote Work Playbook"
    assert edits[1] == "recolor the bullets on slide 2 to purple"
    assert edits[2] == "add a bar chart of office vs remote productivity to slide 3"
    assert edits[3] == "add a vertical flowchart to slide 4 showing plan then build then ship"
    assert edits[4] == "change the subtitle of slide 1 to A field guide for distributed teams"
    assert edits[5] == "delete the chart on slide 3"


# ---------------------------------------------------------------------------
# run_edit_flow — fakes only, no network
# ---------------------------------------------------------------------------

class _FakeDeck:
    """Minimal mutable deck state the fake edit_fn mutates in place."""

    def __init__(self):
        self.slides = [
            {"slide_id": f"s{i}", "slide_index": i, "elements": []} for i in range(4)
        ]

    def state(self):
        return {"title": "Remote Work", "slide_count": len(self.slides), "slides": self.slides}


def _apply(deck: _FakeDeck, instruction: str) -> None:
    """Fake edit application mirroring each EDIT_FLOW instruction's effect."""
    low = instruction.lower()
    if "subtitle of slide 1" in low:
        deck.slides[0]["elements"].append(
            {"objectId": "sub1", "type": "shape", "placeholder": "SUBTITLE",
             "text": "A field guide for distributed teams", "transform": {"translateY": 500_000}}
        )
    elif "title of slide 1" in low:
        deck.slides[0]["elements"] = [
            e for e in deck.slides[0]["elements"] if e.get("placeholder") != "TITLE"
        ] + [{"objectId": "t1", "type": "shape", "placeholder": "TITLE",
              "text": "Remote Work Playbook", "transform": {"translateY": 100_000}}]
    elif "bullets on slide 2" in low:
        deck.slides[1]["elements"] = [
            {"objectId": "b1", "type": "shape", "text_color": "#800080", "text": "• a\n• b"}
        ]
    elif "bar chart" in low:
        deck.slides[2]["elements"].append({"objectId": "chart1", "type": "image"})
    elif "flowchart" in low and "add" in low:
        # Two nodes stacked in Y (vertical) so check_vertical_flowchart's
        # orientation + fits_page checks both pass.
        deck.slides[3]["elements"] += [
            {"objectId": "node_1", "type": "shape",
             "size": {"width": {"magnitude": 1_000_000}, "height": {"magnitude": 500_000}},
             "transform": {"translateX": 4_000_000, "translateY": 500_000, "scaleX": 1, "scaleY": 1}},
            {"objectId": "node_2", "type": "shape",
             "size": {"width": {"magnitude": 1_000_000}, "height": {"magnitude": 500_000}},
             "transform": {"translateX": 4_000_000, "translateY": 2_500_000, "scaleX": 1, "scaleY": 1}},
        ]
    elif "delete the chart" in low:
        deck.slides[2]["elements"] = [
            e for e in deck.slides[2]["elements"] if e.get("type") != "image"
        ]


def _make_fake_env(monkeypatch):
    deck = _FakeDeck()

    async def create_fn(prompt: str) -> dict:
        return {"presentation_id": "PID123", "duration_seconds": 3.0}

    calls = []

    async def edit_fn(pid: str, instruction: str) -> dict:
        calls.append((pid, instruction))
        _apply(deck, instruction)
        return {"duration_seconds": 2.0, "landed_after": True}

    monkeypatch.setattr("app.slidemakr.get_presentation_state", lambda pid: deck.state())
    return deck, create_fn, edit_fn, calls


def test_run_edit_flow_scores_all_six_edits(monkeypatch):
    deck, create_fn, edit_fn, calls = _make_fake_env(monkeypatch)
    result = asyncio.run(efe.run_edit_flow(create_fn, edit_fn, vision=False))

    assert result["deck_id"] == "PID123"
    assert result["created"] is True
    assert "deck_url" in result and "PID123" in result["deck_url"]
    assert len(result["edits"]) == 6
    for i, e in enumerate(result["edits"], start=1):
        assert e["i"] == str(i)
        assert e["landed"] == 1.0, e
        assert e["missing"] == []
        assert 0.0 <= e["overall"] <= 1.0
        # Every sub-score comes with a human-readable reason.
        assert len(e["reasons"]) == 3
        landed_reason, usability_reason, speed_reason = e["reasons"]
        assert landed_reason.startswith("landed 1.0:")
        assert usability_reason.startswith(f"usability {e['usability']}:")
        assert "font consistency" in usability_reason and "overlap" in usability_reason
        assert speed_reason.startswith("speed ")
        assert f"{e['seconds']}s vs {efe.SPEED_TARGET_SECONDS}s target" in speed_reason
    assert 0.0 <= result["avg_overall"] <= 1.0
    assert result["total_seconds"] > 0
    # edit_fn was called once per instruction, on the same deck id throughout.
    assert [pid for pid, _ in calls] == ["PID123"] * 6


def test_run_edit_flow_missed_edit_scores_zero_landed(monkeypatch):
    deck = _FakeDeck()

    async def create_fn(prompt: str) -> dict:
        return {"presentation_id": "PID999"}

    async def edit_fn(pid: str, instruction: str) -> dict:
        # Never actually mutates the deck — every edit "misses".
        return {"duration_seconds": 1.0}

    monkeypatch.setattr("app.slidemakr.get_presentation_state", lambda pid: deck.state())

    result = asyncio.run(efe.run_edit_flow(create_fn, edit_fn, vision=False))
    first = result["edits"][0]
    assert first["landed"] == 0.0
    assert first["missing"]
    assert first["overall"] == 0.0
    landed_reason = first["reasons"][0]
    assert landed_reason.startswith("landed 0.0:")
    assert "title of slide 1" in landed_reason


def test_run_edit_flow_appends_geometry_reason_when_present(monkeypatch):
    # When edit_fn's result carries geometry_before/after (from the runner's
    # geometry self-check), the eval must surface it as its own reason line.
    deck, create_fn, _edit_fn, _calls = _make_fake_env(monkeypatch)

    async def edit_fn(pid: str, instruction: str) -> dict:
        _apply(deck, instruction)
        return {
            "duration_seconds": 2.0, "landed_after": True,
            "geometry_before": 0.3, "geometry_after": 1.0,
        }

    result = asyncio.run(efe.run_edit_flow(create_fn, edit_fn, vision=False))
    first = result["edits"][0]
    assert len(first["reasons"]) == 4
    assert first["reasons"][3] == "geometry before 0.3 \u2192 after 1.0 (min of overlap, fit)"


def test_run_edit_flow_isolates_a_failing_edit(monkeypatch):
    # edit 4 (index 3) raises — the run must still produce all 6 entries, with
    # edit 4 marked as an error and edits 5-6 still applied and scored.
    deck, create_fn, edit_fn, calls = _make_fake_env(monkeypatch)

    real_apply = _apply

    async def flaky_edit_fn(pid: str, instruction: str) -> dict:
        calls.append((pid, instruction))
        if "flowchart" in instruction.lower() and "add" in instruction.lower():
            raise RuntimeError("boom")
        real_apply(deck, instruction)
        return {"duration_seconds": 2.0, "landed_after": True}

    result = asyncio.run(efe.run_edit_flow(create_fn, flaky_edit_fn, vision=False))

    assert len(result["edits"]) == 6
    failed = result["edits"][3]
    assert failed["i"] == "4"
    assert failed["landed"] == 0.0
    assert failed["usability"] is None
    assert failed["overall"] == 0.0
    assert "error" in failed and "boom" in failed["error"]
    assert any("edit failed" in r and "RuntimeError" in r and "boom" in r for r in failed["reasons"])

    # edits 5 and 6 still ran and scored normally (subtitle change + delete).
    assert result["edits"][4]["landed"] == 1.0
    assert result["edits"][5]["landed"] == 1.0
    assert len(calls) == 6  # edit_fn was still called for every instruction
    assert result["avg_overall"] < 1.0  # the failure drags the average down


def test_run_edit_flow_with_deck_id_skips_creation(monkeypatch):
    deck, _create_fn, edit_fn, calls = _make_fake_env(monkeypatch)
    called = {"create": False}

    async def create_fn_spy(prompt: str) -> dict:
        called["create"] = True
        return {"presentation_id": "SHOULD_NOT_BE_USED"}

    result = asyncio.run(
        efe.run_edit_flow(create_fn_spy, edit_fn, deck_id="EXISTING_DECK", vision=False)
    )

    assert called["create"] is False
    assert result["deck_id"] == "EXISTING_DECK"
    assert result["created"] is False
    assert result["create_seconds"] == 0
    assert [pid for pid, _ in calls] == ["EXISTING_DECK"] * 6


def test_run_edit_flow_raises_clearly_when_create_fn_returns_no_deck_id(monkeypatch):
    async def broken_create_fn(prompt: str) -> dict:
        return {}  # no "presentation_id" — create_fn is misbehaving

    async def edit_fn(pid: str, instruction: str) -> dict:
        raise AssertionError("edit_fn must not be called if there's no deck")

    with pytest.raises(RuntimeError, match="create_fn"):
        asyncio.run(efe.run_edit_flow(broken_create_fn, edit_fn, vision=False))


# ---------------------------------------------------------------------------
# _usability_reason — names an image-over-text overlap explicitly
# ---------------------------------------------------------------------------

def test_usability_reason_names_image_over_text_overlap():
    state = {
        "slide_count": 1,
        "slides": [{
            "slide_id": "s1", "slide_index": 0,
            "elements": [
                {"objectId": "body_3", "type": "shape", "text": "hello world",
                 "size": {"width": {"magnitude": 2_000_000}, "height": {"magnitude": 2_000_000}},
                 "transform": {"translateX": 0, "translateY": 0, "scaleX": 1, "scaleY": 1}},
                {"objectId": "image_1", "type": "image",
                 "size": {"width": {"magnitude": 2_000_000}, "height": {"magnitude": 2_000_000}},
                 "transform": {"translateX": 1_000_000, "translateY": 0, "scaleX": 1, "scaleY": 1}},
            ],
        }],
    }
    score, reason = efe._usability_reason(state, 0)
    assert "image_1 covers body_3 (image over text)" in reason
    # Product, not mean: the capped overlap term (0.3) sinks the whole score.
    assert score <= 0.3


# ---------------------------------------------------------------------------
# _vision_score / _vision_reason — re-exported from app.edit_assess, the ONE
# shared overall_quality → score mapping (Task 1.7 fix round 1).
# ---------------------------------------------------------------------------

def test_vision_score_maps_overall_quality():
    assert efe._vision_score({"assessment": {"overall_quality": "good"}}) == 1.0
    assert efe._vision_score({"assessment": {"overall_quality": "needs_fixes"}}) == 0.6
    assert efe._vision_score({"assessment": {"overall_quality": "poor"}}) == 0.3
    assert efe._vision_score({"assessment": {"overall_quality": "unknown"}}) == 0.5
    assert efe._vision_score({}) == 0.5
    assert efe._vision_score(None) == 0.5
    # flat dict (no nested "assessment") is also accepted
    assert efe._vision_score({"overall_quality": "good"}) == 1.0


def test_vision_reason_quotes_summary():
    reason = efe._vision_reason({"assessment": {"overall_quality": "good", "summary": "Clean and legible."}})
    assert reason == 'vision 1.0: "Clean and legible."'


# ---------------------------------------------------------------------------
# run_edit_flow with vision=True (default) — mocks agent.review_slide_layout
# ---------------------------------------------------------------------------

def test_run_edit_flow_vision_default_on_folds_into_usability(monkeypatch):
    deck, create_fn, edit_fn, calls = _make_fake_env(monkeypatch)
    review_calls = []

    def fake_review(pid, slide_id):
        review_calls.append((pid, slide_id))
        return {"assessment": {"overall_quality": "needs_fixes", "summary": "Bullet spacing is uneven."}}

    monkeypatch.setattr("app.agent.review_slide_layout", fake_review)

    result = asyncio.run(efe.run_edit_flow(create_fn, edit_fn))  # vision defaults True

    assert len(review_calls) == 6  # one per edit, on the slide it targeted
    first = result["edits"][0]
    # usability * 0.6 (needs_fixes), rounded
    assert first["reasons"][2] == 'vision 0.6: "Bullet spacing is uneven."'
    assert first["usability"] <= 0.6


def test_run_edit_flow_vision_off_skips_review_call(monkeypatch):
    deck, create_fn, edit_fn, calls = _make_fake_env(monkeypatch)
    review_calls = []
    monkeypatch.setattr(
        "app.agent.review_slide_layout",
        lambda pid, slide_id: review_calls.append((pid, slide_id)) or {},
    )

    result = asyncio.run(efe.run_edit_flow(create_fn, edit_fn, vision=False))

    assert review_calls == []
    assert len(result["edits"][0]["reasons"]) == 3  # landed, usability, speed — no vision line


def test_run_edit_flow_vision_call_failure_degrades_to_unknown(monkeypatch):
    deck, create_fn, edit_fn, calls = _make_fake_env(monkeypatch)

    def _boom(pid, slide_id):
        raise RuntimeError("vision boom")

    monkeypatch.setattr("app.agent.review_slide_layout", _boom)

    result = asyncio.run(efe.run_edit_flow(create_fn, edit_fn))

    first = result["edits"][0]
    assert 'vision 0.5: "vision review failed: vision boom"' in first["reasons"]


def test_run_edit_flow_vision_falls_back_to_touched_slide_when_not_checkable(monkeypatch):
    # A non-checkable instruction (contract can't parse it → touched_idx is
    # None) that the agent still edited: vision must fall back to the LOWEST
    # slide index in the runner's own `touched` telemetry, not skip silently.
    deck, create_fn, _edit_fn, _calls = _make_fake_env(monkeypatch)
    monkeypatch.setattr(efe, "EDIT_FLOW", {**efe.EDIT_FLOW, "edits": ["do something vague to the deck"]})

    review_calls = []

    def fake_review(pid, slide_id):
        review_calls.append((pid, slide_id))
        return {"assessment": {"overall_quality": "good", "summary": "Looks fine."}}

    monkeypatch.setattr("app.agent.review_slide_layout", fake_review)

    async def edit_fn(pid: str, instruction: str) -> dict:
        return {"duration_seconds": 1.0, "touched": {"3": "s4"}}

    result = asyncio.run(efe.run_edit_flow(create_fn, edit_fn))

    assert review_calls == [("PID123", "s4")]
    first = result["edits"][0]
    assert 'vision 1.0: "Looks fine."' in first["reasons"]


def test_run_edit_flow_vision_skipped_reason_when_no_slide_identified(monkeypatch):
    # Non-checkable instruction AND the runner reports no touched slide at
    # all — vision must be skipped with an explicit reason, and the product
    # must NOT include a vision term (equivalent to the deterministic
    # usability score alone).
    deck, create_fn, _edit_fn, _calls = _make_fake_env(monkeypatch)
    monkeypatch.setattr(efe, "EDIT_FLOW", {**efe.EDIT_FLOW, "edits": ["do something vague to the deck"]})

    review_calls = []
    monkeypatch.setattr(
        "app.agent.review_slide_layout",
        lambda pid, slide_id: review_calls.append((pid, slide_id)) or {},
    )

    async def edit_fn(pid: str, instruction: str) -> dict:
        return {"duration_seconds": 1.0}  # no "touched" at all

    result = asyncio.run(efe.run_edit_flow(create_fn, edit_fn))

    assert review_calls == []
    first = result["edits"][0]
    assert (
        "vision skipped: no slide identified for this edit "
        "(not included in the usability product)"
    ) in first["reasons"]
    expected_usability, _ = efe._usability_reason(deck.state(), None)
    assert first["usability"] == expected_usability


def test_run_edit_flow_final_look_reason_present_when_reported(monkeypatch):
    deck, create_fn, _edit_fn, _calls = _make_fake_env(monkeypatch)

    async def edit_fn(pid: str, instruction: str) -> dict:
        _apply(deck, instruction)
        return {
            "duration_seconds": 2.0, "landed_after": True,
            "final_look_ran": True, "final_look_changed": False,
        }

    result = asyncio.run(efe.run_edit_flow(create_fn, edit_fn, vision=False))
    first = result["edits"][0]
    assert first["reasons"][-1] == "final_look ran=True changed=False"
