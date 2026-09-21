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
    rewrites it to simulate an edit landing (or not). `messages` records the
    text sent on each turn so tests can assert on the corrective message.
    """

    def __init__(self, initial_state, turns):
        self.deck = initial_state
        self.turns = list(turns)
        self.calls = 0
        self.messages = []

    def get_state(self, _pid):
        return self.deck

    async def run_turn(self, _runner, _session, msg, images=None):
        self.messages.append(msg)
        stats, state_after = self.turns[self.calls]
        self.calls += 1
        if state_after is not None:
            self.deck = state_after
        return stats


def _install(monkeypatch, script):
    monkeypatch.setattr(slidemakr, "get_presentation_state", script.get_state)
    monkeypatch.setattr(edit_runner, "_run_turn", script.run_turn)


def _run(monkeypatch, script, instruction=RETITLE, rules=None, **overrides):
    for name, val in overrides.items():
        monkeypatch.setattr(edit_runner, name, val)
    _install(monkeypatch, script)
    return asyncio.run(edit_runner.run_text_edit("pid", instruction, rules=rules))


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


def _state_with_body_and_node(title: str, *, covered: bool) -> dict:
    """Slide with a correct title, a text body, and (when `covered`) a node_x
    shape hard-covering 50% of the body — a clean, no-pixels geometry failure:
    overlap gets hard-capped to 0.3 by layout_quality's >10%-over-text rule.
    """
    title_el = {"objectId": "t", "type": "shape", "placeholder": "TITLE",
                "text": title, "transform": {"translateY": 100_000}}
    body = {
        "objectId": "body", "type": "shape", "text": "Body copy",
        "size": {"width": {"magnitude": 2_000_000}, "height": {"magnitude": 2_000_000}},
        "transform": {"translateX": 1_000_000, "translateY": 1_000_000,
                      "scaleX": 1, "scaleY": 1},
    }
    elements = [title_el, body]
    if covered:
        elements.append({
            "objectId": "node_x", "type": "shape", "text": "",
            "size": {"width": {"magnitude": 2_000_000}, "height": {"magnitude": 1_000_000}},
            "transform": {"translateX": 1_000_000, "translateY": 1_000_000,
                          "scaleX": 1, "scaleY": 1},
        })
    return {
        "title": "Deck", "slide_count": 1,
        "slides": [{"slide_id": "s1", "slide_index": 0, "elements": elements}],
    }


def test_geometry_before_is_true_pre_edit_baseline(monkeypatch):
    # geometry_before must come from the state BEFORE the primary turn, not
    # after it — a clean pre-edit slide that the primary turn wrecks must
    # report geometry_before as the CLEAN value, not the already-wrecked one.
    # MAX_CORRECTION_PASSES=0 isolates the before/after capture from any
    # corrective turn.
    script = _Script(_state_with_body_and_node("Q4 Board Review", covered=False), [
        (_turn("aaa"), _state_with_body_and_node("Q4 Board Review", covered=True)),
    ])
    data = _run(monkeypatch, script, MAX_CORRECTION_PASSES=0)
    assert data["correction_passes"] == 0
    assert script.calls == 1
    assert data["geometry_before"] == 1.0
    assert data["geometry_after"] == 0.3


def test_geometry_failure_triggers_one_corrective_turn_naming_the_pair(monkeypatch):
    # Baseline (pre-edit) is CLEAN; the primary turn lands the retitle but
    # leaves node_x covering half of body — the geometry self-check must fire
    # a corrective turn naming the exact overlapping pair, and never suggest
    # deleting. The corrective turn fixes it and the loop stops.
    script = _Script(_state_with_body_and_node("Q4 Board Review", covered=False), [
        (_turn("aaa"), _state_with_body_and_node("Q4 Board Review", covered=True)),   # primary: worsens geometry
        (_turn("bbb"), _state_with_body_and_node("Q4 Board Review", covered=False)),  # corrective: fixed
    ])
    data = _run(monkeypatch, script, MAX_CORRECTION_PASSES=1)
    assert data["correction_passes"] == 1
    assert script.calls == 2
    msg = script.messages[-1]
    assert "node_x" in msg
    assert "body" in msg
    assert "never delete" in msg.lower()
    assert data["geometry_before"] == 1.0
    assert data["geometry_after"] == 1.0


def test_preexisting_geometry_issue_unchanged_does_not_correct(monkeypatch):
    # Baseline guard: the deck ALREADY had node_x over body before this edit
    # (a template flaw). The agent's turn touches that slide but doesn't
    # change the overlap — the edit didn't cause it, so no corrective turn
    # fires; the agent shouldn't be told to rearrange content nobody asked
    # about. geometry_before == geometry_after (both the pre-existing bad
    # value).
    script = _Script(_state_with_body_and_node("Q4 Board Review", covered=True), [
        (_turn("aaa"), None),  # touches slide 0; geometry unchanged
    ])
    data = _run(monkeypatch, script, MAX_CORRECTION_PASSES=1)
    assert data["correction_passes"] == 0
    assert script.calls == 1
    assert data["geometry_before"] == data["geometry_after"] == 0.3


def test_landed_and_clean_geometry_no_correction(monkeypatch):
    script = _Script(_state_with_body_and_node("Q4 Board Review", covered=False), [
        (_turn("aaa"), None),
    ])
    data = _run(monkeypatch, script, MAX_CORRECTION_PASSES=1)
    assert data["correction_passes"] == 0
    assert script.calls == 1
    assert data["geometry_before"] == 1.0
    assert data["geometry_after"] == 1.0


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


# --- Task 1.7: final_look — one look at the end, then fix -----------------


def test_final_look_true_runs_exactly_one_extra_turn_with_image(monkeypatch):
    # Phase A lands cleanly (no correction). final_look=True still runs ONE
    # extra turn after Phase A, with an image attached, and reports it.
    script = _Script(_state("Q4 Board Review"), [
        (_turn("aaa"), None),               # primary edit (lands)
        (_turn("bbb"), None),               # final look: saw it, fixed, committed
    ])
    monkeypatch.setattr(edit_runner, "_render_touched_slides", lambda p, t: [(1, b"PNG")])
    _install(monkeypatch, script)

    data = asyncio.run(edit_runner.run_text_edit("pid", RETITLE, final_look=True))

    assert script.calls == 2
    assert data["vision_passes"] == 1
    assert data["final_look_ran"] is True
    assert data["final_look_changed"] is True
    # correction loop untouched — VISION_SELFCHECK's legacy loop never ran
    assert data["correction_passes"] == 0


def test_final_look_false_runs_no_extra_turn(monkeypatch):
    script = _Script(_state("Q4 Board Review"), [(_turn("aaa"), None)])
    _install(monkeypatch, script)

    data = asyncio.run(edit_runner.run_text_edit("pid", RETITLE, final_look=False))

    assert script.calls == 1  # only the primary turn ran
    assert data["vision_passes"] == 0
    assert data["final_look_ran"] is False
    assert data["final_look_changed"] is False


def test_final_look_none_defaults_to_env(monkeypatch):
    # final_look=None (the default) falls back to SLIDEMAKR_FINAL_LOOK, mirrored
    # here as the module-level FINAL_LOOK flag.
    script = _Script(_state("Q4 Board Review"), [(_turn("aaa"), None)])
    monkeypatch.setattr(edit_runner, "FINAL_LOOK", False)
    _install(monkeypatch, script)

    data = asyncio.run(edit_runner.run_text_edit("pid", RETITLE))

    assert script.calls == 1
    assert data["final_look_ran"] is False


def test_final_look_no_change_when_agent_confirms_good(monkeypatch):
    # The final look turn commits (per _run_turn's ADK semantics) but makes no
    # real edit — no fingerprint → final_look_changed stays False.
    script = _Script(_state("Q4 Board Review"), [
        (_turn("aaa"), None),
        (_text_turn("LOOKS GOOD"), None),
    ])
    monkeypatch.setattr(edit_runner, "_render_touched_slides", lambda p, t: [(1, b"PNG")])
    _install(monkeypatch, script)

    data = asyncio.run(edit_runner.run_text_edit("pid", RETITLE, final_look=True))

    assert data["final_look_ran"] is True
    assert data["final_look_changed"] is False


def test_final_look_capped_at_one_pass_even_with_higher_max_vision_passes(monkeypatch):
    script = _Script(_state("Q4 Board Review"), [
        (_turn("aaa"), None),
        (_turn("bbb"), None),  # final look — only this one extra turn allowed
    ])
    monkeypatch.setattr(edit_runner, "MAX_VISION_PASSES", 5)
    monkeypatch.setattr(edit_runner, "_render_touched_slides", lambda p, t: [(1, b"PNG")])
    _install(monkeypatch, script)

    data = asyncio.run(edit_runner.run_text_edit("pid", RETITLE, final_look=True))

    assert data["vision_passes"] == 1
    assert script.calls == 2


def test_final_look_does_not_run_when_vision_selfcheck_already_ran(monkeypatch):
    # VISION_SELFCHECK's own loop takes precedence; final_look must not add a
    # second, separate look on top of it.
    script = _Script(_state("Q4 Board Review"), [
        (_turn("aaa"), None),
        (_turn("bbb"), None),
        (_text_turn("LOOKS GOOD"), None),
    ])
    monkeypatch.setattr(edit_runner, "VISION_SELFCHECK", True)
    monkeypatch.setattr(edit_runner, "MAX_VISION_PASSES", 2)
    monkeypatch.setattr(edit_runner, "_render_touched_slides", lambda p, t: [(1, b"PNG")])
    _install(monkeypatch, script)

    data = asyncio.run(edit_runner.run_text_edit("pid", RETITLE, final_look=True))

    assert data["vision_passes"] == 2
    assert data["final_look_ran"] is False
    assert script.calls == 3


# --- Task 1.7: baseline_state guard ----------------------------------------


def test_baseline_state_fetch_failure_degrades_to_empty_dict(monkeypatch, caplog):
    # get_presentation_state raising for the PRE-EDIT baseline must not 500 the
    # edit — it should log and degrade to {} (no geometry gate), edit still runs.
    script = _Script(_state("Q4 Board Review"), [(_turn("aaa"), None)])

    calls = {"n": 0}

    def _flaky_get_state(pid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return script.get_state(pid)

    monkeypatch.setattr(slidemakr, "get_presentation_state", _flaky_get_state)
    monkeypatch.setattr(edit_runner, "_run_turn", script.run_turn)

    with caplog.at_level("ERROR"):
        data = asyncio.run(edit_runner.run_text_edit("pid", RETITLE))

    assert script.calls == 1
    # Degraded baseline ({}) still yields a (vacuously clean) score, not a crash.
    assert data["geometry_before"] == 1.0
    assert any("baseline" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Per-user quality rules feeding the corrective loop (Task 1b.3)
# ---------------------------------------------------------------------------

def _state_missing_title() -> dict:
    """One slide with a body element but nothing that reads as a title/heading
    (no TITLE placeholder, no short near-top text) -- fails the `has_title`
    check deterministically.
    """
    body = {"objectId": "body", "type": "shape", "text": "Some longer body copy here",
            "transform": {"translateY": 2_000_000}}
    return {"title": "Deck", "slide_count": 1,
            "slides": [{"slide_id": "s1", "slide_index": 0, "elements": [body]}]}


def _user_has_title_rule():
    from app.quality_rules import Rule, RuleSet, DEFAULT_RULES
    rule = Rule(
        id="user:has_title",
        text="Every slide must have a clear title.",
        scope="user",
        check_id="has_title",
        hard=True,
        source="feedback:2026-01-01T00:00:00",
    )
    return RuleSet(list(DEFAULT_RULES) + [rule])


VAGUE_INSTRUCTION = "make it look nicer"


def test_unmet_hard_user_rule_triggers_one_corrective_turn_naming_the_rule(monkeypatch):
    # Turn 1 leaves the slide without a title (violates the user's saved
    # "has_title" preference); the corrective turn adds one. The instruction
    # itself isn't checkable (vague), and geometry is clean throughout, so
    # only the unmet user rule should drive the correction.
    missing = _state_missing_title()
    fixed = _state_missing_title()
    fixed["slides"][0]["elements"].append(
        {"objectId": "t", "type": "shape", "placeholder": "TITLE", "text": "Q4 Recap",
         "transform": {"translateY": 100_000}}
    )
    script = _Script(missing, [
        (_turn("aaa"), missing),  # primary: still no title
        (_turn("bbb"), fixed),   # corrective: title added
    ])
    data = _run(
        monkeypatch, script, instruction=VAGUE_INSTRUCTION,
        rules=_user_has_title_rule(), MAX_CORRECTION_PASSES=1,
    )
    assert data["correction_passes"] == 1
    assert script.calls == 2
    assert data["user_rules_unmet_before"] == ["user:has_title"]
    assert data["user_rules_unmet_after"] == []
    msg = script.messages[-1]
    assert "This user's preference not met" in msg
    assert "Every slide must have a clear title." in msg
    assert "(slide 1)" in msg


def test_no_user_rules_no_corrective_turn_for_rules(monkeypatch):
    # Same missing-title deck, but no user rules configured (rules=None) —
    # existing behavior must be unchanged: no corrective turn fires just
    # because a slide lacks a title (has_title isn't a universal DEFAULT_RULE).
    missing = _state_missing_title()
    script = _Script(missing, [(_turn("aaa"), None)])
    data = _run(
        monkeypatch, script, instruction=VAGUE_INSTRUCTION,
        rules=None, MAX_CORRECTION_PASSES=1,
    )
    assert data["correction_passes"] == 0
    assert script.calls == 1
    assert data["user_rules_unmet_before"] == []
    assert data["user_rules_unmet_after"] == []
