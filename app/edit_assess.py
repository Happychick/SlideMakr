"""
SlideMakr — shared edit assessment (the ONE "did the edit land / is it usable"
judge, used both LIVE and in the eval).

Historically the "did the instruction land?" checks lived inside the offline
eval (`app/edit_eval`). That made the eval a report card the agent never saw at
runtime. This module lifts that judgement out so the SAME code runs in two
places:

  * `app/edit_runner.run_text_edit` calls `build_edit_contract` + `assess` after
    every edit turn to decide, in real time, whether to self-correct.
  * `app/edit_eval` imports the `check_*` / `usability` primitives so the number
    we report is produced by the exact code the agent optimizes against.

Import safety: this module only imports the leaf modules (`layout_quality`,
`instruction_contract`) at module load, and resolves brand palettes via a lazy
import of `app.eval`, so it introduces no import cycle with `edit_runner` /
`edit_eval` (which both import *this*).
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from .instruction_contract import NUMBER_WORDS, _number_from_text


# ---------------------------------------------------------------------------
# Tunables (config-driven — override via env, mirroring SLIDEMAKR_BATCH_MODE)
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


# A deck is "correct enough" when this fraction of the contract's requirements
# pass. Default 1.0 = every checkable requirement must land, else self-correct.
LANDED_THRESHOLD: float = _env_float("SLIDEMAKR_LANDED_THRESHOLD", 1.0)

# Graded brand-match is fuzzy; a recolor requirement counts as landed at/above
# this brand-match score.
_RECOLOR_PASS_AT: float = _env_float("SLIDEMAKR_RECOLOR_PASS_AT", 0.5)


# ---------------------------------------------------------------------------
# State accessors + deterministic checks (moved verbatim from edit_eval so the
# eval and the live runner share one implementation).
# ---------------------------------------------------------------------------

def _slide(state: dict, index: int) -> dict:
    slides = (state or {}).get("slides", [])
    return slides[index] if 0 <= index < len(slides) else {}


def check_title_text(state: dict, slide_index: int, expected_substring: str) -> float:
    """1.0 if the slide's title (or any short text) contains the expected text."""
    want = (expected_substring or "").lower().strip()
    if not want:
        return 0.0
    for el in _slide(state, slide_index).get("elements", []):
        text = str(el.get("text", "")).lower()
        if want in text:
            return 1.0
    return 0.0


def check_vertical_flowchart(state: dict, slide_index: int) -> float:
    """1.0 if the slide has a vertical, on-page flowchart; else 0.0/0.5.

    Combines presence (node_ shapes), orientation, and fit.
    """
    from . import layout_quality as lq
    els = _slide(state, slide_index).get("elements", [])
    nodes = [e for e in els if str(e.get("objectId", "")).startswith("node_")]
    if not nodes:
        return 0.0
    oriented = lq.flowchart_orientation(els) == "vertical"
    fits = lq.fits_page_score(els) >= 0.99
    return 1.0 if (oriented and fits) else 0.5


def check_has_flowchart(state: dict, slide_index: int) -> float:
    """1.0 if the slide has a flowchart (node_ shapes), orientation-agnostic."""
    els = _slide(state, slide_index).get("elements", [])
    return 1.0 if any(str(e.get("objectId", "")).startswith("node_") for e in els) else 0.0


def check_slide_colors_on_brand(state: dict, slide_index: int, brand_colors: list) -> float:
    """Brand-match a slide's colours — both shape fills AND text colours."""
    from . import layout_quality as lq
    els = _slide(state, slide_index).get("elements", [])
    colors = [e["fill_color"] for e in els if e.get("fill_color")]
    colors += [e["text_color"] for e in els if e.get("text_color")]
    return lq.brand_match_score(colors, brand_colors)


def check_has_title(state: dict, slide_index: int) -> float:
    """1.0 if the slide has a real title — a TITLE placeholder or a short heading
    near the top. Flowchart node/edge labels do NOT count as titles.
    """
    for e in _slide(state, slide_index).get("elements", []):
        oid = str(e.get("objectId", ""))
        if oid.startswith("node_") or oid.startswith("edge_"):
            continue
        if e.get("placeholder") in ("TITLE", "CENTERED_TITLE"):
            return 1.0
        t = str(e.get("text", "")).strip()
        y = (e.get("transform") or {}).get("translateY")
        near_top = isinstance(y, (int, float)) and y < 1_000_000
        if t and len(t) < 60 and "\n" not in t and near_top:
            return 1.0
    return 0.0


def check_has_bullets(state: dict, slide_index: int) -> float:
    """1.0 if the slide has multi-line / bulleted text."""
    for e in _slide(state, slide_index).get("elements", []):
        text = str(e.get("text", ""))
        if "\n" in text or "•" in text:
            return 1.0
    return 0.0


def check_has_image(state: dict, slide_index: int) -> float:
    """1.0 if the slide has an image element."""
    return 1.0 if any(
        e.get("type") == "image" for e in _slide(state, slide_index).get("elements", [])
    ) else 0.0


def check_has_chart(state: dict, slide_index: int) -> float:
    """1.0 if the slide has a chart — rendered as an image element."""
    els = _slide(state, slide_index).get("elements", [])
    return 1.0 if any(e.get("type") == "image" for e in els) else 0.0


def usability(state: dict, slide_index: int) -> float:
    """Deterministic slide quality (0-1): font consistency, balance, fit, title."""
    from . import layout_quality as lq
    els = _slide(state, slide_index).get("elements", [])
    font = lq.font_consistency_score(state)
    balance = lq.balance_score(els)
    layout = (lq.fits_page_score(els) + lq.overlap_score(els)) / 2 if els else 1.0
    titled = check_has_title(state, slide_index)
    return round((font + balance + layout + titled) / 4, 4)


# ---------------------------------------------------------------------------
# Edit-instruction contract — turn a loose edit instruction into a small set of
# deterministically-checkable requirements. Anything we can't parse → the
# contract is `checkable: False` and the runner falls back to a bounded model
# self-check instead of a deterministic gate.
# ---------------------------------------------------------------------------

_SLIDE_RE = re.compile(
    r"\bslide\s+(\d+|" + "|".join(NUMBER_WORDS) + r")\b", flags=re.I
)
_COLOR_WORDS = (
    "red", "orange", "yellow", "green", "blue", "purple", "violet", "pink",
    "teal", "cyan", "magenta", "black", "white", "gray", "grey", "gold",
)

# Plain colour words resolve locally — no web brand lookup needed for these.
_COLOR_WORD_HEX = {
    "red": "#FF0000", "orange": "#FFA500", "yellow": "#FFFF00",
    "green": "#008000", "blue": "#0000FF", "purple": "#800080",
    "violet": "#8F00FF", "pink": "#FFC0CB", "teal": "#008080",
    "cyan": "#00FFFF", "magenta": "#FF00FF", "black": "#000000",
    "white": "#FFFFFF", "gray": "#808080", "grey": "#808080",
    "gold": "#FFD700",
}


def _first_slide_index(text: str, default: Optional[int]) -> Optional[int]:
    """First 'slide N' in the text as a 0-based index (N is human 1-based)."""
    m = _SLIDE_RE.search(text)
    if not m:
        return default
    n = _number_from_text(m.group(1))
    return (n - 1) if n and n > 0 else default


def _after_to(text: str) -> str:
    """The phrase after the last ' to ' — the target value of a change."""
    parts = re.split(r"\bto\b", text, flags=re.I)
    return parts[-1].strip(" \t\"'.,") if len(parts) > 1 else ""


def build_edit_contract(instruction: str) -> Dict[str, Any]:
    """Parse an edit instruction into checkable requirements.

    Returns {"requirements": [...], "checkable": bool, "source": instruction}.
    Each requirement: {kind, slide_index, pass_at, ...kind-specific}.
    """
    text = (instruction or "").strip()
    low = text.lower()
    reqs: List[Dict[str, Any]] = []

    # --- retitle: "change/set/rename the title of slide N to X" ---------------
    # Requires a rename verb BEFORE "title" (so "move the title to the left"
    # and "the title is huge" don't fire), splits on the FIRST " to " after
    # "title" (so "...color on slide 2 to blue" and multi-"to" values like
    # "Welcome to Acme" resolve correctly), and rejects a colour word or a
    # "the ..." value (those are position/color edits, not a retitle).
    title_idx = low.find("title")
    if title_idx != -1 and re.search(r"\b(change|set|rename|retitle|update)\b", low[:title_idx]):
        after_title = text[title_idx + len("title"):]
        to_m = re.search(r"\bto\b", after_title, flags=re.I)
        if to_m:
            between = after_title[:to_m.start()].lower()
            value = after_title[to_m.end():].strip(" \t\"'.,")
            value_low = value.lower()
            if (
                value
                and "color" not in between
                and "colour" not in between
                and value_low not in _COLOR_WORDS
                and not value_low.startswith("the ")
            ):
                reqs.append({
                    "kind": "retitle",
                    "slide_index": _first_slide_index(low, 0),
                    "value": value,
                    "pass_at": 1.0,
                })

    # --- recolor: "recolor ... slide N ... to <brand/color>" ------------------
    if re.search(r"\b(recolou?r|colou?r)\b", low) and "title" not in low:
        idx = _first_slide_index(low, None)
        target = _after_to(text) or next(
            (w for w in _COLOR_WORDS if w in low), ""
        )
        if idx is not None and target:
            reqs.append({
                "kind": "recolor",
                "slide_index": idx,
                "brand": target,
                "pass_at": _RECOLOR_PASS_AT,
            })

    # --- add an element: "add/insert a <kind> to slide N" ---------------------
    if re.search(r"\b(add|insert|put|create)\b", low):
        idx = _first_slide_index(low, None)
        if idx is not None:
            if "flowchart" in low or "flow chart" in low:
                reqs.append({
                    "kind": "flowchart",
                    "slide_index": idx,
                    "vertical": "vertical" in low,
                    "pass_at": 1.0,
                })
            elif "chart" in low:
                reqs.append({"kind": "chart", "slide_index": idx, "pass_at": 1.0})
            elif "bullet" in low:
                reqs.append({"kind": "bullets", "slide_index": idx, "pass_at": 1.0})
            elif "image" in low or "photo" in low or "picture" in low:
                reqs.append({"kind": "image", "slide_index": idx, "pass_at": 1.0})

    return {"requirements": reqs, "checkable": bool(reqs), "source": instruction}


def _resolve_brand_colors(req: Dict[str, Any], ctx: Optional[dict]) -> list:
    """Palette for a recolor requirement — from ctx if provided, else looked up.

    ctx (supplied by the eval) short-circuits the web lookup; the live product
    path falls back to the same branding tool the eval uses.
    """
    if ctx and ctx.get("brand_colors"):
        return ctx["brand_colors"]
    brand = (req.get("brand") or "").strip()
    if not brand:
        return []
    hex_val = _COLOR_WORD_HEX.get(brand.lower())
    if hex_val:
        return [hex_val]
    try:  # lazy — avoids an import cycle and the network hit in unit tests
        from .eval import _brand_palette
        return _brand_palette(brand)
    except Exception:  # noqa: BLE001 — absence just means recolor scores 0
        return []


def _score_requirement(req: Dict[str, Any], state: dict, ctx: Optional[dict]) -> float:
    kind = req["kind"]
    idx = req["slide_index"]
    if kind == "retitle":
        return check_title_text(state, idx, req.get("value", ""))
    if kind == "recolor":
        return check_slide_colors_on_brand(state, idx, _resolve_brand_colors(req, ctx))
    if kind == "flowchart":
        return check_vertical_flowchart(state, idx) if req.get("vertical") \
            else check_has_flowchart(state, idx)
    if kind == "chart":
        return check_has_chart(state, idx)
    if kind == "bullets":
        return check_has_bullets(state, idx)
    if kind == "image":
        return check_has_image(state, idx)
    return 0.0


def describe_missing(req: Dict[str, Any]) -> str:
    """One-line, agent-facing description of an unmet requirement."""
    slide_no = req["slide_index"] + 1
    kind = req["kind"]
    if kind == "retitle":
        return f"the title of slide {slide_no} is not '{req.get('value','')}'"
    if kind == "recolor":
        return f"slide {slide_no} is not recoloured to {req.get('brand','the requested colour')}"
    if kind == "flowchart":
        v = "vertical " if req.get("vertical") else ""
        return f"slide {slide_no} has no {v}flowchart"
    return f"slide {slide_no} is missing the requested {kind}"


def describe_observed(req: Dict[str, Any], state: dict) -> str:
    """What is ACTUALLY on the slide now for this requirement — the read-after-write
    signal that lets a corrective turn close the gap instead of repeating itself.
    """
    idx = req["slide_index"]
    els = _slide(state, idx).get("elements", [])
    if req["kind"] == "retitle":
        for e in els:
            if e.get("placeholder") in ("TITLE", "CENTERED_TITLE"):
                return f"title currently reads '{str(e.get('text','')).strip()[:80]}'"
        return "no title element found on this slide"
    if req["kind"] == "recolor":
        colors = [e.get("fill_color") for e in els if e.get("fill_color")]
        colors += [e.get("text_color") for e in els if e.get("text_color")]
        colors = [c for c in colors if c]
        return f"colours currently on the slide: {', '.join(colors[:8]) or 'none detected'}"
    # presence-style requirements
    kinds = sorted({e.get("type") for e in els if e.get("type")})
    return f"slide currently has: {', '.join(kinds) or 'nothing'}"


def assess(
    instruction: str,
    state: dict,
    contract: Optional[Dict[str, Any]] = None,
    ctx: Optional[dict] = None,
) -> Dict[str, Any]:
    """Judge whether an edit instruction landed on `state`.

    Returns:
      checkable: was the instruction parseable into deterministic checks?
      landed:    fraction of requirements that passed (None if not checkable)
      missing:   requirements that did NOT pass (the corrective work list)
      usability: min deterministic usability across the touched slides
    """
    contract = contract or build_edit_contract(instruction)
    reqs = contract.get("requirements", [])
    if not contract.get("checkable") or not reqs:
        return {"checkable": False, "landed": None, "missing": [], "usability": None}

    missing: List[Dict[str, Any]] = []
    passed = 0
    for req in reqs:
        if _score_requirement(req, state, ctx) >= req.get("pass_at", 1.0):
            passed += 1
        else:
            missing.append(req)

    idxs = sorted({r["slide_index"] for r in reqs})
    usab = round(min((usability(state, i) for i in idxs), default=1.0), 4)
    return {
        "checkable": True,
        "landed": round(passed / len(reqs), 4),
        "missing": missing,
        "usability": usab,
        "requirements": reqs,
    }
