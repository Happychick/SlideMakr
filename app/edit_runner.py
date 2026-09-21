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
    usability_parts,
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
# How many see-and-fix passes the agent gets (render -> look -> fix -> re-render).
MAX_VISION_PASSES: int = _env_int("SLIDEMAKR_MAX_VISION_PASSES", 2)
# One look at the touched slides AFTER Phase A finishes, regardless of
# VISION_SELFCHECK (which governs the legacy per-turn loop). Capped at ONE pass.
FINAL_LOOK: bool = _env_bool("SLIDEMAKR_FINAL_LOOK", False)


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


def _render_touched_slides(
    presentation_id: str, touched: Dict[Any, str], cap: int = 3
) -> List[tuple]:
    """Render the slides the agent actually EDITED so it can SEE them.

    `touched` maps slide_index -> slide_id, collected from each commit's
    verification — so this works even when the instruction couldn't be parsed
    into a contract (e.g. STT garbled it). Returns [(slide_number, png_bytes), ...];
    a failed render is simply skipped. Capped so a deck-wide edit stays bounded.
    """
    from . import slidemakr
    out: List[tuple] = []
    for idx in sorted(k for k in touched if k is not None)[:cap]:
        slide_id = touched[idx]
        try:
            png = slidemakr.get_slide_thumbnail(presentation_id, slide_id, "LARGE")
        except Exception:  # noqa: BLE001 — render is best-effort
            png = None
        if png:
            out.append((idx + 1, png))
    return out


def _see_and_fix_message(instruction: str, slide_numbers: List[int], presentation_id: str) -> str:
    nums = ", ".join(str(n) for n in slide_numbers)
    return (
        f"Attached is a screenshot of slide {nums} as it looks right now, after your edits. "
        "LOOK at it.\n\n"
        f'The user asked: "{instruction}".\n\n'
        "Judge it like a designer looking at the actual slide:\n"
        "- Did the requested change actually happen?\n"
        "- Is every piece of text clearly READABLE — enough contrast against whatever is "
        "behind it (a dark title on a dark background is invisible)?\n"
        "- Anything overlapping, running off the slide, or misaligned?\n\n"
        "If it already looks good, reply exactly 'LOOKS GOOD' and change nothing.\n"
        f"If not, fix ONLY what's wrong with the narrow tools, then call commit_edits('{presentation_id}'). "
        "Don't repeat an edit that didn't help — if something's still wrong after your last try, "
        "look again and try a different fix."
    )


# A touched slide fails the geometry self-check when overlap is hard-capped
# (something covers >10% of a text element — layout_quality's cap) or an
# element sits off-page. No rendering: both signals come straight off the
# state the agent already gets back from commit_edits.
_GEOMETRY_OVERLAP_OK = 0.3
_GEOMETRY_FIT_OK = 0.99


def _slide_elements(state: dict, slide_index: int) -> List[Dict[str, Any]]:
    slides = (state or {}).get("slides", [])
    return slides[slide_index].get("elements", []) if 0 <= slide_index < len(slides) else []


def _off_page_ids(elements: List[Dict[str, Any]]) -> List[str]:
    """objectIds of elements that sit (even partly) off the slide bounds."""
    from . import layout_quality as lq
    out = []
    for el in elements:
        box = lq._box(el)  # noqa: SLF001 — internal EMU box math, same package
        if not box:
            continue
        x, y, w, h = box
        if x < 0 or y < 0 or x + w > lq.SLIDE_W_EMU or y + h > lq.SLIDE_H_EMU:
            out.append(str(el.get("objectId", "?")))
    return out


def _geometry_score(state: dict, touched_indices) -> Optional[float]:
    """min(overlap, fit) across the touched slides — one number that also
    reflects an off-page (fit) failure, not just the overlap hard cap.
    `None` when nothing was touched (no target to score).
    """
    idxs = sorted({i for i in touched_indices if i is not None})
    if not idxs:
        return None
    return min(
        min(usability_parts(state, idx)["overlap"], usability_parts(state, idx)["fit"])
        for idx in idxs
    )


def _slide_is_geometry_clean(parts: Dict[str, float]) -> bool:
    return parts["overlap"] > _GEOMETRY_OVERLAP_OK and parts["fit"] >= _GEOMETRY_FIT_OK


def _geometry_verdict(state: dict, baseline_state: dict, touched_indices) -> Dict[str, Any]:
    """Geometry gate over the touched slides, relative to the PRE-EDIT baseline.

    A touched slide fires an issue only when it is bad NOW and either (a) it
    got WORSE than its pre-edit baseline (this edit caused it), or (b) it was
    clean before and isn't now. A slide that was already bad in the baseline
    and is unchanged by the edit does NOT fire — that's a pre-existing
    template flaw, not something this edit broke, and the agent shouldn't be
    sent off to rearrange content nobody asked about.

    Vacuously OK when nothing was touched this turn.

    Returns {"ok": bool, "issues": [...]} where each issue is {"slide_no":
    1-based, "details": [overlap_details lines], "off_page": [objectIds]} for
    a failing slide, ready for the corrective message builder.
    """
    idxs = sorted({i for i in touched_indices if i is not None})
    if not idxs:
        return {"ok": True, "issues": []}

    from . import layout_quality as lq

    issues: List[Dict[str, Any]] = []
    for idx in idxs:
        cur = usability_parts(state, idx)
        if _slide_is_geometry_clean(cur):
            continue
        base = usability_parts(baseline_state, idx)
        got_worse = min(cur["overlap"], cur["fit"]) < min(base["overlap"], base["fit"])
        was_clean = _slide_is_geometry_clean(base)
        if not (got_worse or was_clean):
            continue  # pre-existing, unchanged by this edit — not our problem to fix
        overlap_bad = cur["overlap"] <= _GEOMETRY_OVERLAP_OK
        fit_bad = cur["fit"] < _GEOMETRY_FIT_OK
        els = _slide_elements(state, idx)
        issues.append({
            "slide_no": idx + 1,
            "details": lq.overlap_details(els) if overlap_bad else [],
            "off_page": _off_page_ids(els) if fit_bad else [],
        })
    return {"ok": not issues, "issues": issues}


def _corrective_message(
    presentation_id: str,
    missing: List[Dict[str, Any]],
    state: dict,
    geometry_issues: Optional[List[Dict[str, Any]]] = None,
) -> str:
    # Read-after-write: name what's MISSING and what's actually on the slide now, so
    # the agent closes the gap instead of blindly repeating a failed edit. When the
    # geometry self-check also failed, one message covers both — naming the exact
    # overlapping pairs / off-page objectIds instead of a bare score.
    sections: List[str] = []
    if missing:
        bullets = "\n".join(
            f"- {describe_missing(r)} — {describe_observed(r, state)}" for r in missing
        )
        sections.append(
            "Your edits are in, but these requested changes did NOT land (with what's "
            f"on the slide now):\n{bullets}"
        )
    if geometry_issues:
        lines: List[str] = []
        for issue in geometry_issues:
            for d in issue["details"]:
                lines.append(f"on slide {issue['slide_no']}, {d}")
            for oid in issue["off_page"]:
                lines.append(f"on slide {issue['slide_no']}, {oid} is off the edge of the slide")
        bullets = "\n".join(f"- {l}" for l in lines)
        sections.append(
            "The layout also has a problem:\n"
            f"{bullets}\n\n"
            "Move or resize the covering element into empty space, or shrink the "
            "covered text box; never delete user content."
        )
    sections.append(
        f"Fix ONLY the items above using the narrow tools, then call "
        f"commit_edits('{presentation_id}'). Do not touch anything that already "
        "succeeded, and do not repeat an edit you already made — if your last "
        "attempt didn't change the result, try a different approach."
    )
    return "\n\n".join(sections)


async def _run_turn(
    runner, session, message_text: str, images: Optional[List[bytes]] = None
) -> Dict[str, Any]:
    """Run one agent turn to completion; return that turn's stats.

    Accumulates request counts, whether commit_edits was called, the reply text,
    tokens used, and the fingerprint of the committed request set (for the
    anti-repeat guard). When `images` is given (PNG bytes), they're attached to the
    message so the multimodal edit agent SEES its own slide, not a text description.
    """
    from google.genai import types

    parts = [types.Part.from_bytes(data=png, mime_type="image/png") for png in (images or [])]
    parts.append(types.Part.from_text(text=message_text))
    content = types.Content(role="user", parts=parts)
    turn: Dict[str, Any] = {
        "total_requests": 0, "success_count": 0, "committed": False,
        "reply": "", "tokens": 0, "fingerprint": None, "touched": {},
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
                    # Track which slides this commit actually touched, so Phase B can
                    # SEE them even when the instruction wasn't parseable into a contract.
                    ver = resp.get("verification")
                    if isinstance(ver, dict):
                        for ts in ver.get("touched_slides", []) or []:
                            sid = ts.get("slide_id")
                            if sid:
                                turn["touched"][ts.get("slide_index")] = sid
    return turn


def _merge_turn(data: Dict[str, Any], turn: Dict[str, Any]) -> None:
    data["total_requests"] += turn["total_requests"]
    data["success_count"] += turn["success_count"]
    data["tokens"] += turn["tokens"]
    data["committed"] = data["committed"] or turn["committed"]
    if turn["reply"]:
        data["reply"] += turn["reply"]
    for si, sid in turn.get("touched", {}).items():
        data["touched"][si] = sid


def _safe_assess(instruction, state, contract, ctx) -> Dict[str, Any]:
    """assess() wrapped so a bug there can't 500 an edit that already landed.

    A raised exception is logged and treated as "not checkable" — the same
    shape assess() itself returns when it can't parse the instruction — so
    the caller's checkable/landed/missing/usability handling stays uniform.
    """
    try:
        return assess(instruction, state, contract, ctx)
    except Exception:  # noqa: BLE001 — the edit succeeded; don't fail the request
        logger.exception("assess() raised during edit self-correction; treating as not checkable")
        return {"checkable": False, "landed": None, "missing": [], "usability": None}


async def run_text_edit(
    presentation_id: str,
    instruction: str,
    ctx: Optional[dict] = None,
    final_look: Optional[bool] = None,
) -> Dict[str, Any]:
    """Apply a text instruction to a deck, self-correcting if it didn't land.

    `ctx` optionally carries assessment context (e.g. {"brand_colors": [...]})
    so a recolor check skips the web lookup; the product path passes None and the
    assessment resolves palettes itself.

    `final_look`: when True, run Phase B (see-and-fix) exactly ONCE after Phase A
    finishes, regardless of VISION_SELFCHECK — capped at one vision pass for this
    path. When None (default), falls back to the SLIDEMAKR_FINAL_LOOK env var.
    VISION_SELFCHECK keeps its existing meaning for the legacy per-turn loop below.

    Returns run stats, including the self-correction telemetry the eval surfaces:
    landed_before/after, correction_passes, repeated_fix, tokens, duration,
    final_look_ran, final_look_changed.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from . import slidemakr

    svc = InMemorySessionService()
    runner = Runner(agent=build_text_edit_agent(), app_name="edit_runner", session_service=svc)
    session = await svc.create_session(app_name="edit_runner", user_id="edit_runner")

    contract = build_edit_contract(instruction)
    # Fixed for the life of this call — the TRUE pre-edit baseline every
    # geometry comparison is measured against, never reassigned.
    try:
        baseline_state = slidemakr.get_presentation_state(presentation_id)
    except Exception:  # noqa: BLE001 — degrade to no geometry gate, edit still runs
        logger.exception("get_presentation_state failed for pre-edit baseline; degrading to {}")
        baseline_state = {}
    state = baseline_state

    do_final_look = FINAL_LOOK if final_look is None else final_look

    data: Dict[str, Any] = {
        "total_requests": 0, "success_count": 0, "committed": False, "reply": "",
        "tokens": 0, "correction_passes": 0, "repeated_fix": False,
        "checkable": contract["checkable"], "landed_before": None, "landed_after": None,
        "vision_passes": 0, "touched": {},
        "geometry_before": None, "geometry_after": None,
        "final_look_ran": False, "final_look_changed": False,
    }
    start = time.time()

    def _within_budget() -> bool:
        if EDIT_TOKEN_BUDGET and data["tokens"] >= EDIT_TOKEN_BUDGET:
            return False
        if EDIT_TIME_BUDGET_S and (time.time() - start) >= EDIT_TIME_BUDGET_S:
            return False
        return True

    # --- Turn 1: the primary edit -------------------------------------------
    first = await _run_turn(runner, session, _initial_message(presentation_id, state, instruction))
    _merge_turn(data, first)
    fingerprints: List[str] = [first["fingerprint"]] if first["fingerprint"] else []

    # --- Phase A: did the instruction land, AND is the geometry clean? -------
    # (both cheap, deterministic — no rendering; geometry comes straight off the
    # state the agent's own commit_edits already returned in `touched`).
    # geometry_before is scored against `baseline_state` (pre-edit, fixed) —
    # NOT the post-turn-1 state — so it's a true baseline, not already-wrecked.
    state = slidemakr.get_presentation_state(presentation_id)
    a = _safe_assess(instruction, state, contract, ctx)
    data["landed_before"] = data["landed_after"] = a["landed"]

    data["geometry_before"] = _geometry_score(baseline_state, data["touched"].keys())
    data["geometry_after"] = _geometry_score(state, data["touched"].keys())
    geom = _geometry_verdict(state, baseline_state, data["touched"].keys())

    def _needs_correction(assessment: Dict[str, Any], geometry: Dict[str, Any]) -> bool:
        landed_miss = assessment["checkable"] and (assessment["landed"] or 0.0) < LANDED_THRESHOLD
        return landed_miss or not geometry["ok"]

    while (
        _needs_correction(a, geom)
        and data["correction_passes"] < MAX_CORRECTION_PASSES
        and _within_budget()
    ):
        turn = await _run_turn(
            runner, session,
            _corrective_message(presentation_id, a["missing"], state, geom["issues"]),
        )
        _merge_turn(data, turn)
        data["correction_passes"] += 1
        if turn["fingerprint"] and turn["fingerprint"] in fingerprints:
            data["repeated_fix"] = True
            break
        if turn["fingerprint"]:
            fingerprints.append(turn["fingerprint"])
        state = slidemakr.get_presentation_state(presentation_id)
        a = _safe_assess(instruction, state, contract, ctx)
        data["landed_after"] = a["landed"]
        data["geometry_after"] = _geometry_score(state, data["touched"].keys())
        geom = _geometry_verdict(state, baseline_state, data["touched"].keys())

    # --- Phase B: SEE it and fix it -----------------------------------------
    # The agent looks at its OWN rendered slide (real pixels, not a text critique)
    # and self-corrects formatting/legibility, iterating until it looks good or the
    # budget runs out. Triggered by what the agent actually EDITED (touched slides),
    # NOT by whether we could parse the instruction — so an STT-garbled request whose
    # contract didn't parse still gets seen. Gated off by default behind
    # SLIDEMAKR_VISION_SELFCHECK until the eval says it's worth the render+turn cost.
    if VISION_SELFCHECK and data["touched"]:
        while data["vision_passes"] < MAX_VISION_PASSES and _within_budget():
            shots = _render_touched_slides(presentation_id, data["touched"])
            if not shots:
                break
            turn = await _run_turn(
                runner,
                session,
                _see_and_fix_message(instruction, [n for n, _ in shots], presentation_id),
                images=[png for _, png in shots],
            )
            _merge_turn(data, turn)
            data["vision_passes"] += 1
            # Agent looked and changed nothing (no real commit) → it judged it good.
            if not turn["fingerprint"]:
                break
            if turn["fingerprint"] in fingerprints:
                data["repeated_fix"] = True
                break
            fingerprints.append(turn["fingerprint"])
            state = slidemakr.get_presentation_state(presentation_id)
    elif do_final_look and data["touched"] and data["vision_passes"] < 1 and _within_budget():
        # ONE look at the end regardless of VISION_SELFCHECK — capped at one pass.
        shots = _render_touched_slides(presentation_id, data["touched"])
        if shots:
            turn = await _run_turn(
                runner,
                session,
                _see_and_fix_message(instruction, [n for n, _ in shots], presentation_id),
                images=[png for _, png in shots],
            )
            _merge_turn(data, turn)
            data["vision_passes"] += 1
            data["final_look_ran"] = True
            if turn["fingerprint"] and turn["fingerprint"] not in fingerprints:
                fingerprints.append(turn["fingerprint"])
                data["final_look_changed"] = True
                state = slidemakr.get_presentation_state(presentation_id)
            elif turn["fingerprint"] and turn["fingerprint"] in fingerprints:
                data["repeated_fix"] = True

    data["duration_seconds"] = round(time.time() - start, 2)
    # Slide indices are ints internally (sortable, comparable); key by str for
    # the HTTP boundary so JSON round-trips the same shape it started with.
    data["touched"] = {str(k): v for k, v in data["touched"].items()}
    return data
