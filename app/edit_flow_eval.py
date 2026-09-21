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


def _speed_reason(speed_score: float, seconds: float) -> str:
    return f"speed {round(speed_score, 4)}: {seconds}s vs {SPEED_TARGET_SECONDS}s target"


async def run_edit_flow(
    create_fn: CreateFn,
    edit_fn: EditFn,
    *,
    deck_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create one deck (or reuse `deck_id`), apply EDIT_FLOW["edits"] in order,
    scoring each against the deck state read back after it lands.

    `edit_fn` is `app.edit_runner.run_text_edit` in production — called as
    `edit_fn(deck_id, instruction)` — and `create_fn` is
    `evals.run.default_generate_fn`, called as `create_fn(prompt)`.
    """
    from . import slidemakr  # lazy: keeps this module import-cheap for tests

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

            edit_result = await edit_fn(deck_id, instruction)
            state = slidemakr.get_presentation_state(deck_id)
        except Exception as e:  # noqa: BLE001 — isolate one bad edit from the rest of the deck
            edits.append({
                "i": str(i),
                "instruction": instruction,
                "landed": 0.0,
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
        usability_score, usability_reason = _usability_reason(state, touched_idx)
        speed = _speed_score(float(edit_result.get("duration_seconds") or 0.0))
        seconds = float(edit_result.get("duration_seconds") or 0.0)
        overall = round((landed or 0.0) * usability_score * speed, 4)

        reasons = [
            _landed_reason(landed, verdict["missing"], state),
            usability_reason,
            _speed_reason(speed, seconds),
        ]
        geometry_before = edit_result.get("geometry_before")
        geometry_after = edit_result.get("geometry_after")
        if geometry_before is not None or geometry_after is not None:
            reasons.append(
                f"geometry before {geometry_before} \u2192 after {geometry_after} "
                "(min of overlap, fit)"
            )

        edits.append({
            "i": str(i),
            "instruction": instruction,
            "landed": landed,
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
