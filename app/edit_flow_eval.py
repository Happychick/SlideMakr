"""
SlideMakr — one-deck edit-flow eval, with a hard deck budget.

Prior eval design created a fresh deck per edit case (and per variant), which
meant a single run could spin up 50+ decks with zero cost control. This module
replaces that shape for day-to-day edit scoring: ONE deck is created (or an
existing one reused via `--deck`), then a FIXED script of six text edits is
applied to that SAME deck, in order, each scored with the shared judge in
`app.edit_assess` (the same code the live agent runs against).

Kept intentionally small: `run_edit_flow` takes `create_fn`/`edit_fn` so it
never imports Google/ADK clients itself and stays trivially unit-testable
with fakes (see tests/test_edit_flow_eval.py). The real wiring — reusing
`evals.run.default_generate_fn` and `app.edit_runner.run_text_edit` — lives in
`evals/run.py`, which also enforces the deck budget before any network call.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from . import edit_assess
from . import quality_rules as qr


# A single 4-slide prompt about remote work: title, benefits bullets,
# challenges, takeaways — deliberately plain so the six scripted edits below
# have stable, well-known targets (slide 1 title/subtitle, slide 2 bullets,
# slide 3 chart, slide 4 flowchart).
EDIT_FLOW: Dict[str, Any] = {
    "create": (
        "Create a 4-slide presentation about remote work: "
        "slide 1 a title slide, "
        "slide 2 the benefits of remote work as bullet points, "
        "slide 3 the challenges of remote work, "
        "slide 4 key takeaways."
    ),
    "edits": [
        "change the title of slide 1 to Remote Work Playbook",
        "recolor the bullets on slide 2 to purple",
        "add a bar chart of office vs remote productivity to slide 3",
        "add a vertical flowchart to slide 4 showing plan then build then ship",
        "change the subtitle of slide 1 to A field guide for distributed teams",
        "delete the chart on slide 3",
    ],
}

# speed = min(1, SPEED_TARGET_SECONDS / seconds) — an edit at or under this
# many seconds scores full speed credit.
SPEED_TARGET_SECONDS = 20


CreateFn = Callable[[str], Awaitable[Dict[str, Any]]]
EditFn = Callable[[str, str], Awaitable[Dict[str, Any]]]


def _url(deck_id: str) -> str:
    return f"https://docs.google.com/presentation/d/{deck_id}/edit"


def _speed_score(seconds: float) -> float:
    if seconds <= 0:
        return 1.0
    return min(1.0, SPEED_TARGET_SECONDS / seconds)


def _landed_reason(landed: float, missing: List[Dict[str, Any]], state: dict) -> str:
    """One sentence: what was required vs what's actually on the slide now."""
    if not missing:
        return f"landed {landed}: all requirements met"
    parts = [
        f"{edit_assess.describe_missing(req)} — {edit_assess.describe_observed(req, state)}"
        for req in missing
    ]
    return f"landed {landed}: " + "; ".join(parts)


def _usability_reason(state: dict, slide_index: Optional[int]) -> tuple[float, str]:
    """`edit_assess.usability_parts`'s sub-scores for one slide, plus a sentence
    explaining each — including naming any image-over-text overlap
    (`layout_quality.overlap_details`), which is the failure a bare number hides.

    Kept as a standalone helper (still directly unit-tested) even though
    `run_edit_flow` itself now scores "looks good" via `RuleSet.looks_good`
    (see `_looks_good_reasons` below) — this one stays useful for callers that
    want the deterministic layout sub-scores without a RuleSet.
    """
    from . import layout_quality as lq

    if slide_index is None:
        return 1.0, "usability 1.0: no slide touched"

    slides = (state or {}).get("slides", [])
    els = slides[slide_index].get("elements", []) if 0 <= slide_index < len(slides) else []
    parts = edit_assess.usability_parts(state, slide_index)
    score = edit_assess.usability(state, slide_index)

    overlap_str = str(parts["overlap"])
    details = lq.overlap_details(els)
    if details:
        overlap_str += f" (slide {slide_index + 1}: " + "; ".join(details) + ")"
    reason = (
        f"usability {score}: font consistency {parts['font']}, balance {parts['balance']}, "
        f"fit {parts['fit']}, overlap {overlap_str}, title {parts['title']}"
    )
    return score, reason


def _looks_good(
    ruleset: "qr.RuleSet", state: dict, slide_index: Optional[int]
) -> tuple[float, float, List[str]]:
    """(looks_good_baseline, looks_good_user, reasons) for one slide, one
    `rule <id> <score>` line per rule the RuleSet actually scored (see
    `RuleSet.score` — a rule with `check_id=None` is prompt/vision-only and
    isn't scored here). The `no_overlap`/`overlap` rule's line also names the
    exact overlapping pair, mirroring `_usability_reason`'s overlap detail.
    `slide_index=None` (nothing touched/checkable) scores both 1.0 vacuously.
    """
    if slide_index is None:
        return 1.0, 1.0, ["looks_good 1.0/1.0: no slide touched"]

    from . import layout_quality as lq

    slides = (state or {}).get("slides", [])
    els = slides[slide_index].get("elements", []) if 0 <= slide_index < len(slides) else []
    overlap_detail = "; ".join(lq.overlap_details(els))

    scores = ruleset.score(state, slide_index)
    baseline, user = ruleset.looks_good(state, slide_index)
    id_to_rule = {r.id: r for r in ruleset.rules}

    reasons: List[str] = []
    for bucket in ("universal", "user"):
        for rid, score in scores[bucket].items():
            rule = id_to_rule.get(rid)
            line = f"rule {rid} {score:.2f}"
            if rule is not None and rule.check_id == "overlap" and overlap_detail:
                line += f": {overlap_detail}"
            reasons.append(line)
    return baseline, user, reasons


def _slowest_step(timeline: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not timeline:
        return None
    return max(timeline, key=lambda t: t.get("seconds") or 0.0)


def _speed_reason(speed_score: float, seconds: float, timeline: Optional[List[Dict[str, Any]]] = None) -> str:
    reason = f"speed {round(speed_score, 4)}: {seconds}s vs {SPEED_TARGET_SECONDS}s target"
    slow = _slowest_step(timeline or [])
    if slow is not None:
        tool_calls = slow.get("tool_calls")
        suffix = f" ({tool_calls} tool calls)" if tool_calls is not None else ""
        reason += f" — slowest: {slow['step']} {slow['seconds']}s{suffix}"
    return reason


# vision_score/vision_reason — the ONE overall_quality → score mapping — now
# live in app.edit_assess (shared with app.edit_eval). Re-exported under the
# old private names so existing call sites/tests keep working unchanged.
_vision_score = edit_assess.vision_score
_vision_reason = edit_assess.vision_reason


def _final_look_reason(edit_result: Dict[str, Any]) -> Optional[str]:
    ran = edit_result.get("final_look_ran")
    if ran is None:
        return None
    return f"final_look ran={ran} changed={edit_result.get('final_look_changed', False)}"


def _vision_target(
    state: dict, touched_idx: Optional[int], edit_result: Dict[str, Any]
) -> Optional[tuple]:
    """The (slide_index, slide_id) to run the vision review against.

    Prefers the contract's touched slide (when the instruction parsed).
    Falls back to the LOWEST slide `run_text_edit` actually touched — its
    `touched` dict (str slide_index -> slide_id) — so a non-checkable
    instruction (e.g. STT-garbled) that still edited a slide gets a vision
    pass too, instead of silently skipping it. `None` when neither source
    names a slide.
    """
    slides = (state or {}).get("slides", [])
    if touched_idx is not None and 0 <= touched_idx < len(slides):
        slide_id = slides[touched_idx].get("slide_id")
        if slide_id:
            return touched_idx, slide_id

    touched = edit_result.get("touched") or {}
    if touched:
        idx = min(int(k) for k in touched.keys())
        return idx, touched[str(idx)]

    return None


async def run_edit_flow(
    create_fn: CreateFn,
    edit_fn: EditFn,
    *,
    deck_id: Optional[str] = None,
    vision: bool = True,
    rules: Optional["qr.RuleSet"] = None,
    prompt_rules: bool = True,
) -> Dict[str, Any]:
    """Create one deck (or reuse `deck_id`), apply EDIT_FLOW["edits"] in order,
    scoring each against the deck state read back after it lands.

    `edit_fn` is `app.edit_runner.run_text_edit` in production — called as
    `edit_fn(deck_id, instruction)` — and `create_fn` is
    `evals.run.default_generate_fn`, called as `create_fn(prompt)`.

    `vision`: when True (default), each edit gets ONE `agent.review_slide_layout`
    call against the slide it targeted, folded into `overall` as one more
    product term (`overall *= vision score`), with a reason line quoting the
    reviewer's one-line summary. Pass False (or `--no-vision` in evals/run.py)
    to skip it — e.g. in tests, or to isolate the deterministic score.

    `rules`: the `RuleSet` to score `looks_good_baseline`/`looks_good_user`
    against — defaults to `RuleSet()` (universal defaults only, no user rules)
    when `None`. `prompt_rules`: when True (default) and `rules` is given, the
    SAME RuleSet is also passed to `edit_fn(..., rules=...)` so the agent's
    prompt carries it; pass `prompt_rules=False` to score against `rules` but
    still call `edit_fn` without it — the "does the prompt help" control.
    """
    from . import slidemakr  # lazy: keeps this module import-cheap for tests

    ruleset = rules if rules is not None else qr.RuleSet()
    created = deck_id is None
    create_seconds = 0.0
    if created:
        create_start = time.time()
        created_deck = await create_fn(EDIT_FLOW["create"])
        create_seconds = round(time.time() - create_start, 2)
        deck_id = created_deck.get("presentation_id")
        if deck_id is None:
            raise RuntimeError(
                "create_fn returned no 'presentation_id' — cannot run the edit "
                "script against a deck that doesn't exist"
            )

    edits: List[Dict[str, Any]] = []
    for i, instruction in enumerate(EDIT_FLOW["edits"], start=1):
        contract = edit_assess.build_edit_contract(instruction)
        touched_idx = contract["requirements"][0]["slide_index"] if contract["requirements"] else None

        try:
            # Delete requirements need to know what existed BEFORE the edit —
            # otherwise "there's nothing there" scores a free 1.0 whether the
            # edit actually removed something or there was never anything to
            # remove. Snapshot only for delete kind; every other kind ignores
            # `ctx["before_ids"]`.
            ctx = None
            if contract["requirements"] and contract["requirements"][0]["kind"] == "delete":
                before_state = slidemakr.get_presentation_state(deck_id)
                target = contract["requirements"][0]["target"]
                ctx = {"before_ids": {touched_idx: edit_assess.delete_target_ids(
                    before_state, touched_idx, target
                )}}

            if rules is not None:
                edit_result = await edit_fn(deck_id, instruction, rules=(rules if prompt_rules else None))
            else:
                edit_result = await edit_fn(deck_id, instruction)
            state = slidemakr.get_presentation_state(deck_id)
        except Exception as e:  # noqa: BLE001 — isolate one bad edit from the rest of the deck
            edits.append({
                "i": str(i),
                "instruction": instruction,
                "landed": 0.0,
                "looks_good_baseline": None,
                "looks_good_user": None,
                "usability": None,
                "seconds": 0.0,
                "overall": 0.0,
                "missing": [],
                "error": str(e),
                "reasons": [f"edit failed: {type(e).__name__}: {e}"],
            })
            continue

        verdict = edit_assess.assess(instruction, state, contract=contract, ctx=ctx)

        landed = verdict["landed"] if verdict["checkable"] else 0.0
        looks_good_baseline, looks_good_user, rule_reasons = _looks_good(ruleset, state, touched_idx)
        # `usability` kept as an alias of the looks-good product for one
        # release -- see evals/README.md.
        usability_score = round(looks_good_baseline * looks_good_user, 4)

        reasons = [_landed_reason(landed, verdict["missing"], state)]
        reasons.extend(rule_reasons)

        vision_score = 1.0
        if vision:
            target = _vision_target(state, touched_idx, edit_result)
            if target is None:
                reasons.append(
                    "vision skipped: no slide identified for this edit "
                    "(not included in the usability product)"
                )
            else:
                _idx, slide_id = target
                try:
                    from .agent import review_slide_layout
                    review = review_slide_layout(deck_id, slide_id)
                except Exception as e:  # noqa: BLE001 \u2014 vision is best-effort
                    review = {"assessment": {"overall_quality": "unknown",
                                              "summary": f"vision review failed: {e}"}}
                vision_score = _vision_score(review)
                reasons.append(_vision_reason(review))

        timeline = edit_result.get("timeline")
        speed = _speed_score(float(edit_result.get("duration_seconds") or 0.0))
        seconds = float(edit_result.get("duration_seconds") or 0.0)
        overall = round(
            (landed or 0.0) * looks_good_baseline * looks_good_user * speed
            * (vision_score if vision else 1.0),
            4,
        )
        reasons.append(_speed_reason(speed, seconds, timeline))

        geometry_before = edit_result.get("geometry_before")
        geometry_after = edit_result.get("geometry_after")
        if geometry_before is not None or geometry_after is not None:
            reasons.append(
                f"geometry before {geometry_before} \u2192 after {geometry_after} "
                "(min of overlap, fit)"
            )
        final_look_reason = _final_look_reason(edit_result)
        if final_look_reason is not None:
            reasons.append(final_look_reason)

        edits.append({
            "i": str(i),
            "instruction": instruction,
            "landed": landed,
            "looks_good_baseline": looks_good_baseline,
            "looks_good_user": looks_good_user,
            "usability": usability_score,
            "seconds": seconds,
            "overall": overall,
            "missing": verdict["missing"],
            "reasons": reasons,
        })

    # Reported seconds (not wall-clock, which can round to 0 under fakes/mocks) —
    # the sum of the create call plus every edit's own duration_seconds.
    total_seconds = round(create_seconds + sum(e["seconds"] for e in edits), 2)
    avg_overall = round(sum(e["overall"] for e in edits) / len(edits), 4) if edits else 0.0

    return {
        "deck_id": deck_id,
        "deck_url": _url(deck_id) if deck_id else None,
        "created": created,
        "create_seconds": create_seconds,
        "edits": edits,
        "avg_overall": avg_overall,
        "total_seconds": total_seconds,
    }
