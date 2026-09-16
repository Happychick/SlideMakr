"""
Tests for the real-time self-correction controller in edit_runner.run_text_edit.

The agent turn (`_run_turn`) and the deck read (`slidemakr.get_presentation_state`)
are monkeypatched with a scripted fake, so we exercise the controller's decision
logic — assess → correct → guard — with no model, no Google API, no network.
"""

from __future__ import annotations

import asyncio

import pytest

from app import edit_runner, slidemakr


RETITLE = "change the title of slide 1 to Q4 Board Review"


@pytest.fixture(autouse=True)
def _no_vision(monkeypatch):
    # Keep the controller tests hermetic by default — no rendering/vision network.
    # The vision-path test re-enables by monkeypatching _vision_review directly.
    monkeypatch.setattr(edit_runner, "VISION_SELFCHECK", False)


def _state(title: str) -> dict:
    return {
        "title": "Deck", "slide_count": 1,
        "slides": [{
            "slide_id": "s1", "slide_index": 0,
            "elements": [{"objectId": "t", "type": "shape", "placeholder": "TITLE",
                          "text": title, "transform": {"translateY": 100_000}}],
        }],
    }


def _turn(fingerprint: str, tokens: int = 100) -> dict:
    # `touched` = the slides this commit edited (index -> id), from commit verification.
    return {"total_requests": 1, "success_count": 1, "committed": True,
            "reply": "", "tokens": tokens, "fingerprint": fingerprint, "touched": {0: "s1"}}


class _Script:
    """Drives get_presentation_state and _run_turn from a scripted timeline.

    `deck` is the live state assess() reads; each scripted turn optionally
    rewrites it to simulate an edit landing (or not).
    """

    def __init__(self, initial_state, turns):
        self.deck = initial_state
        self.turns = list(turns)
        self.calls = 0

    def get_state(self, _pid):
        return self.deck

    async def run_turn(self, _runner, _session, _msg, images=None):
        stats, state_after = self.turns[self.calls]
        self.calls += 1
        if state_after is not None:
            self.deck = state_after
        return stats


def _install(monkeypatch, script):
    monkeypatch.setattr(slidemakr, "get_presentation_state", script.get_state)
    monkeypatch.setattr(edit_runner, "_run_turn", script.run_turn)


def _run(monkeypatch, script, **overrides):
    for name, val in overrides.items():
        monkeypatch.setattr(edit_runner, name, val)
    _install(monkeypatch, script)
    return asyncio.run(edit_runner.run_text_edit("pid", RETITLE))


def test_landed_first_try_no_correction(monkeypatch):
    script = _Script(_state("Q4 Board Review"), [(_turn("aaa"), None)])
    data = _run(monkeypatch, script)
    assert data["correction_passes"] == 0
    assert data["landed_before"] == 1.0
    assert data["landed_after"] == 1.0
    assert data["repeated_fix"] is False
    assert script.calls == 1  # only the primary turn ran


def test_missing_then_corrected_once(monkeypatch):
    # Turn 1 leaves the wrong title; the corrective turn fixes it.
    script = _Script(_state("Original Title"), [
        (_turn("aaa"), _state("Original Title")),   # primary: miss
        (_turn("bbb"), _state("Q4 Board Review")),  # corrective: fixed
    ])
    data = _run(monkeypatch, script, MAX_CORRECTION_PASSES=1)
    assert data["correction_passes"] == 1
    assert data["landed_before"] == 0.0
    assert data["landed_after"] == 1.0
    assert data["repeated_fix"] is False
    assert script.calls == 2


def test_repeated_identical_fix_stops(monkeypatch):
    # Corrective turn commits the SAME request set as the primary → no progress.
    script = _Script(_state("Original Title"), [
        (_turn("aaa"), _state("Original Title")),   # primary: miss, fp aaa
        (_turn("aaa"), _state("Original Title")),   # corrective: identical fp, still miss
        (_turn("ccc"), _state("Q4 Board Review")),  # (should never run)
    ])
    data = _run(monkeypatch, script, MAX_CORRECTION_PASSES=3)
    assert data["repeated_fix"] is True
    assert data["correction_passes"] == 1  # stopped after the repeat, no 3rd turn
    assert script.calls == 2


def test_zero_passes_never_corrects(monkeypatch):
    script = _Script(_state("Original Title"), [(_turn("aaa"), _state("Original Title"))])
    data = _run(monkeypatch, script, MAX_CORRECTION_PASSES=0)
    assert data["correction_passes"] == 0
    assert data["landed_after"] == 0.0
    assert script.calls == 1


def test_token_budget_blocks_correction(monkeypatch):
    # Primary burns past the token ceiling → no corrective turn despite the miss.
    script = _Script(_state("Original Title"), [
        (_turn("aaa", tokens=999), _state("Original Title")),
        (_turn("bbb"), _state("Q4 Board Review")),  # (should never run)
    ])
    data = _run(monkeypatch, script, MAX_CORRECTION_PASSES=1, EDIT_TOKEN_BUDGET=500)
    assert data["correction_passes"] == 0
    assert script.calls == 1


def _text_turn(reply):
    """A see-and-fix turn where the agent looked and changed NOTHING (no commit)."""
    return {"total_requests": 0, "success_count": 0, "committed": True,
            "reply": reply, "tokens": 50, "fingerprint": None}


def test_agent_sees_its_slide_fixes_then_confirms(monkeypatch):
    # Instruction lands on the first turn. Then the agent LOOKS at its rendered
    # slide: pass 1 it sees a problem and fixes it; pass 2 it looks again and says
    # it's good, changing nothing → the loop stops.
    script = _Script(_state("Q4 Board Review"), [
        (_turn("aaa"), None),                 # primary edit (lands)
        (_turn("bbb"), None),                 # see-and-fix pass 1: saw it, fixed, committed
        (_text_turn("LOOKS GOOD"), None),     # see-and-fix pass 2: looked, good, no change
    ])
    monkeypatch.setattr(edit_runner, "VISION_SELFCHECK", True)
    monkeypatch.setattr(edit_runner, "MAX_VISION_PASSES", 2)
    # Feed the agent a (fake) rendered image each pass — the point is it SEES.
    monkeypatch.setattr(edit_runner, "_render_touched_slides", lambda p, t: [(1, b"PNG")])
    monkeypatch.setattr(slidemakr, "get_presentation_state", script.get_state)
    monkeypatch.setattr(edit_runner, "_run_turn", script.run_turn)
    data = asyncio.run(edit_runner.run_text_edit("pid", RETITLE))

    assert data["landed_before"] == 1.0     # instruction landed immediately
    assert data["vision_passes"] == 2       # fixed once, then confirmed good
    assert data["correction_passes"] == 0   # no instruction-landing correction needed
    assert script.calls == 3


def test_vision_runs_even_when_contract_not_checkable(monkeypatch):
    # The decouple: STT garbled the instruction so the contract won't parse, but the
    # agent still EDITED a slide — Phase B must SEE it and fix it anyway (the flowchart
    # case that used to be skipped).
    script = _Script(_state("whatever"), [
        (_turn("aaa"), None),                 # primary edit touches slide 0
        (_turn("bbb"), None),                 # see-and-fix pass 1: saw it, fixed
        (_text_turn("LOOKS GOOD"), None),     # pass 2: looked, good, no change
    ])
    monkeypatch.setattr(edit_runner, "VISION_SELFCHECK", True)
    monkeypatch.setattr(edit_runner, "MAX_VISION_PASSES", 2)
    monkeypatch.setattr(edit_runner, "_render_touched_slides", lambda p, t: [(1, b"PNG")])
    monkeypatch.setattr(slidemakr, "get_presentation_state", script.get_state)
    monkeypatch.setattr(edit_runner, "_run_turn", script.run_turn)
    data = asyncio.run(edit_runner.run_text_edit("pid", "At a vertical flow chart to slide three"))

    assert data["checkable"] is False   # contract did NOT parse...
    assert data["vision_passes"] == 2   # ...but the agent still saw + fixed it
    assert script.calls == 3


def test_assess_exception_does_not_500_a_successful_edit(monkeypatch):
    # assess() raising must not blow up run_text_edit — the edit itself
    # succeeded; the runner should log and fall back to checkable=False for
    # that assessment, not raise (which the HTTP layer turns into a 500).
    script = _Script(_state("Q4 Board Review"), [(_turn("aaa"), None)])
    _install(monkeypatch, script)

    def _boom(*_a, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(edit_runner, "assess", _boom)
    data = asyncio.run(edit_runner.run_text_edit("pid", RETITLE))
    assert data["landed_before"] is None
    assert data["landed_after"] is None
    assert script.calls == 1


def test_non_checkable_instruction_skips_controller(monkeypatch):
    # "make it nicer" → not checkable → no deterministic correction loop.
    script = _Script(_state("Whatever"), [(_turn("aaa"), None)])
    monkeypatch.setattr(slidemakr, "get_presentation_state", script.get_state)
    monkeypatch.setattr(edit_runner, "_run_turn", script.run_turn)
    data = asyncio.run(edit_runner.run_text_edit("pid", "make it look nicer"))
    assert data["checkable"] is False
    assert data["correction_passes"] == 0
    assert data["landed_before"] is None
    assert script.calls == 1
