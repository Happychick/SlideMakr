"""
SlideMakr — text-driven edit runner (the STT→text voice-swap engine) with a
real-time, cost-aware self-correction controller.

Applies a plain-text instruction to an existing deck via the narrow edit tools,
driven by gemini-2.5-flash (a text model that reliably reads tool responses —
unlike the native-audio model). This is the single implementation used by BOTH
the eval harness (app/edit_eval) and the product endpoints (/edit-audio,
/edit-text), so what we measure is exactly what we ship.

The controller embeds the eval's OWN judgement into the live flow (app/edit_assess):
after the agent edits and commits, we re-read the deck and `assess` whether each
requested change actually landed. If something is still missing we run ONE more
targeted turn naming only the misses — but hard guards keep it cheap:

  * MAX_CORRECTION_PASSES  — hard cap on corrective turns (default 1)
  * anti-repeat            — if a corrective turn commits the identical request
                             set as a prior turn, stop (no progress)
  * token + time budget    — stop once either ceiling is crossed

Because the controller uses the same `assess` the eval scores with, and the
eval's speed pillar penalises the extra correction time, an over-eager loop
scores WORSE — the metric self-regulates the design.

Credential-agnostic: it edits whatever presentation the caller points it at using
the credentials already set on the slidemakr context (service account for eval,
the user's OAuth for the product).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from .edit_assess import (
    LANDED_THRESHOLD,
    assess,
    build_edit_contract,
    describe_missing,
    describe_observed,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Controller guards (config-driven — override via env, no redeploy of logic)
# ---------------------------------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name, "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


# How many corrective turns are allowed AFTER the first edit turn.
MAX_CORRECTION_PASSES: int = _env_int("SLIDEMAKR_MAX_CORRECTION_PASSES", 1)
# Cumulative-token ceiling for the whole edit (0 = unlimited).
EDIT_TOKEN_BUDGET: int = _env_int("SLIDEMAKR_EDIT_TOKEN_BUDGET", 200_000)
# Wall-clock ceiling in seconds for the whole edit (0 = unlimited).
EDIT_TIME_BUDGET_S: int = _env_int("SLIDEMAKR_EDIT_TIME_BUDGET_S", 90)
# Let the agent SEE its rendered slide (vision review) and self-correct formatting,
# not just check whether the instruction landed.
#
# DEFAULT OFF — the 2026-08-10 edit eval refuted reviewing every turn: ~7x slower
# (8-13s -> 65-87s) tanked the speed pillar (avg 0.65 -> 0.32), the design critic
# fired a costly corrective turn on EVERY slide (even a trivial retitle), and it
# still didn't fix the flowchart contrast. The idea (agent sees + fixes) is right;
# the trigger is wrong. A viable version reviews ONCE at the end of a whole request
# with a much higher bar (act on "poor" only), and must be re-measured before it
# ships on. See .claude/artifacts + FUTURE.md.
VISION_SELFCHECK: bool = _env_bool("SLIDEMAKR_VISION_SELFCHECK", False)


def build_text_edit_agent():
    from google.adk import Agent
    from .agent import edit_agent, EDIT_INSTRUCTION
    return Agent(
        model="gemini-2.5-flash",
        name="slidemakr_text_editor",
        description="Text-driven editor (STT→text voice pipeline + eval harness)",
        instruction=EDIT_INSTRUCTION,
        tools=edit_agent.tools,
    )


def _initial_message(presentation_id: str, state: dict, instruction: str) -> str:
    return (
        f"You are editing presentation '{state.get('title', '')}' (ID: {presentation_id}). "
        f"It has {state.get('slide_count', 0)} slides. Current state:\n"
        f"{json.dumps(state, indent=2)[:8000]}\n\n"
        f"User instruction: {instruction}\n"
        f"Apply it using the narrow tools, then call commit_edits('{presentation_id}')."
    )


def _vision_review(
    presentation_id: str, contract: Dict[str, Any], state: dict
) -> List[str]:
    """The agent's EYES: render each edited slide and let the vision reviewer look.

    Returns a flat list of human-readable "slide N: problem (where) -> fix" strings
    for slides the reviewer rates needs_fixes/poor. Empty when vision self-check is
    off, the instruction wasn't checkable, or every slide looks good. Best-effort —
    a failed render/review never blocks the edit.
    """
    if not VISION_SELFCHECK or not contract.get("checkable"):
        return []
    from .agent import review_slide_layout  # lazy — avoids import cycle

    slides = state.get("slides", [])
    issues: List[str] = []
    for idx in sorted({r["slide_index"] for r in contract.get("requirements", [])}):
        if not (0 <= idx < len(slides)):
            continue
        slide_id = slides[idx].get("slide_id")
        if not slide_id:
            continue
        try:
            review = review_slide_layout(presentation_id, slide_id)
        except Exception:  # noqa: BLE001 — vision is best-effort
            continue
        assessment = review.get("assessment") if isinstance(review, dict) else None
        if not isinstance(assessment, dict):
            assessment = review if isinstance(review, dict) else {}
        if assessment.get("overall_quality") not in ("needs_fixes", "poor"):
            continue
        for it in assessment.get("issues", []) or []:
            prob = str(it.get("problem", "")).strip()
            loc = str(it.get("location", "")).strip()
            fix = str(it.get("fix", "")).strip()
            if prob:
                issues.append(f"slide {idx + 1}: {prob}"
                              + (f" ({loc})" if loc else "")
                              + (f" -> {fix}" if fix else ""))
    return issues


def _corrective_message(
    presentation_id: str,
    missing: List[Dict[str, Any]],
    state: dict,
    vision_issues: Optional[List[str]] = None,
) -> str:
    # Read-after-write: name what's MISSING (with what's actually there now) AND what
    # LOOKS wrong in the rendered slide, so the agent closes both gaps instead of
    # blindly repeating a failed edit.
    parts: List[str] = []
    if missing:
        parts.append("These requested changes did NOT land (with what's on the slide now):")
        parts += [f"- {describe_missing(r)} — {describe_observed(r, state)}" for r in missing]
    if vision_issues:
        parts.append("These formatting problems are visible in the rendered slide — fix them:")
        parts += [f"- {v}" for v in vision_issues]
    body = "\n".join(parts)
    return (
        f"Your edits are in, but there's more to do:\n{body}\n\n"
        f"Fix ONLY the items above using the narrow tools, then call "
        f"commit_edits('{presentation_id}'). Do not touch anything that already "
        "succeeded, and do not repeat an edit you already made — if your last "
        "attempt didn't change the result, try a different approach."
    )


async def _run_turn(runner, session, message_text: str) -> Dict[str, Any]:
    """Run one agent turn to completion; return that turn's stats.

    Accumulates request counts, whether commit_edits was called, the reply text,
    tokens used, and the fingerprint of the committed request set (for the
    anti-repeat guard).
    """
    from google.genai import types

    content = types.Content(role="user", parts=[types.Part.from_text(text=message_text)])
    turn: Dict[str, Any] = {
        "total_requests": 0, "success_count": 0, "committed": False,
        "reply": "", "tokens": 0, "fingerprint": None,
    }
    async for event in runner.run_async(
        user_id="edit_runner", session_id=session.id, new_message=content
    ):
        usage = getattr(event, "usage_metadata", None)
        if usage is not None:
            turn["tokens"] += getattr(usage, "total_token_count", 0) or 0
        if not (event.content and event.content.parts):
            continue
        for part in event.content.parts:
            if part.text:
                turn["reply"] += part.text
            if part.function_call and part.function_call.name == "commit_edits":
                turn["committed"] = True
            if part.function_response:
                resp = part.function_response.response
                if isinstance(resp, dict):
                    if "success_count" in resp:
                        turn["total_requests"] += resp.get("total", 0)
                        turn["success_count"] += resp.get("success_count", 0)
                    if resp.get("request_fingerprint"):
                        turn["fingerprint"] = resp["request_fingerprint"]
    return turn


def _merge_turn(data: Dict[str, Any], turn: Dict[str, Any]) -> None:
    data["total_requests"] += turn["total_requests"]
    data["success_count"] += turn["success_count"]
    data["tokens"] += turn["tokens"]
    data["committed"] = data["committed"] or turn["committed"]
    if turn["reply"]:
        data["reply"] += turn["reply"]


async def run_text_edit(
    presentation_id: str,
    instruction: str,
    ctx: Optional[dict] = None,
) -> Dict[str, Any]:
    """Apply a text instruction to a deck, self-correcting if it didn't land.

    `ctx` optionally carries assessment context (e.g. {"brand_colors": [...]})
    so a recolor check skips the web lookup; the product path passes None and the
    assessment resolves palettes itself.

    Returns run stats, including the self-correction telemetry the eval surfaces:
    landed_before/after, correction_passes, repeated_fix, tokens, duration.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from . import slidemakr

    svc = InMemorySessionService()
    runner = Runner(agent=build_text_edit_agent(), app_name="edit_runner", session_service=svc)
    session = await svc.create_session(app_name="edit_runner", user_id="edit_runner")

    contract = build_edit_contract(instruction)
    state = slidemakr.get_presentation_state(presentation_id)

    data: Dict[str, Any] = {
        "total_requests": 0, "success_count": 0, "committed": False, "reply": "",
        "tokens": 0, "correction_passes": 0, "repeated_fix": False,
        "checkable": contract["checkable"], "landed_before": None, "landed_after": None,
        "vision_issues_before": None, "vision_issues_after": None,
    }
    start = time.time()

    # --- Turn 1: the primary edit -------------------------------------------
    first = await _run_turn(runner, session, _initial_message(presentation_id, state, instruction))
    _merge_turn(data, first)
    fingerprints: List[str] = [first["fingerprint"]] if first["fingerprint"] else []

    # --- Assess (did it land?) + SEE (does it look right?) -------------------
    state = slidemakr.get_presentation_state(presentation_id)
    a = assess(instruction, state, contract, ctx)
    vissues = _vision_review(presentation_id, contract, state)
    data["landed_before"] = data["landed_after"] = a["landed"]
    data["vision_issues_before"] = data["vision_issues_after"] = len(vissues)

    def _within_budget() -> bool:
        if EDIT_TOKEN_BUDGET and data["tokens"] >= EDIT_TOKEN_BUDGET:
            return False
        if EDIT_TIME_BUDGET_S and (time.time() - start) >= EDIT_TIME_BUDGET_S:
            return False
        return True

    def _needs_fix() -> bool:
        # Correct if the instruction didn't land OR the slide visibly looks wrong.
        return (a["landed"] or 0.0) < LANDED_THRESHOLD or bool(vissues)

    while (
        a["checkable"]
        and _needs_fix()
        and data["correction_passes"] < MAX_CORRECTION_PASSES
        and _within_budget()
    ):
        turn = await _run_turn(
            runner,
            session,
            _corrective_message(presentation_id, a["missing"], state, vissues),
        )
        _merge_turn(data, turn)
        data["correction_passes"] += 1

        # Anti-repeat: a corrective turn that commits the identical request set
        # as a prior turn is making no progress — stop.
        if turn["fingerprint"] and turn["fingerprint"] in fingerprints:
            data["repeated_fix"] = True
            break
        if turn["fingerprint"]:
            fingerprints.append(turn["fingerprint"])

        state = slidemakr.get_presentation_state(presentation_id)
        a = assess(instruction, state, contract, ctx)
        vissues = _vision_review(presentation_id, contract, state)
        data["landed_after"] = a["landed"]
        data["vision_issues_after"] = len(vissues)

    data["duration_seconds"] = round(time.time() - start, 2)
    return data
