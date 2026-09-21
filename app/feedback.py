"""Classify a post-deck comment as a one-off edit or a standing preference.

Fast path: regex-detected deck-local references ("slide 3", "this", "here",
"that") always mean "edit this deck now" -- no LLM call. Otherwise we ask the
model to decide and, when it's a preference, to rewrite it as one imperative
rule with an optional quality-check binding.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any, Dict

logger = logging.getLogger(__name__)

# "slide 3", "this", "here", "that" -> almost always about the deck on screen.
_EDIT_HINT_RE = re.compile(r"\bslide\s+\d+\b|\bthis\b|\bhere\b|\bthat\b", re.IGNORECASE)


def slug_rule_id(text: str) -> str:
    """Slugify feedback text into a short, filesystem/Firestore-safe id.

    Lowercase, non `[a-z0-9_-]` chars become `-`, runs collapse, edges trim,
    result is capped at 64 chars. A blank result falls back to a random id
    so callers never get an empty/degenerate document id.
    """
    slug = re.sub(r"[^a-z0-9_-]+", "-", (text or "").lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")
    slug = slug[:64].strip("-_")
    if not slug:
        slug = uuid.uuid4().hex[:8]
    return slug


def _fallback() -> Dict[str, Any]:
    return {"kind": "edit", "rule_text": None, "check_id": None, "params": {}, "hard": False}


def classify_feedback(text: str) -> Dict[str, Any]:
    """Decide whether `text` is a one-off edit or a standing preference.

    Returns a dict with keys: kind ("preference"|"edit"), rule_text,
    check_id (a `quality_rules.CHECKS` key or None), params (dict), hard
    (bool). Any failure (regex miss + LLM error, bad JSON, etc.) fails safe
    to `{"kind": "edit", ...}` so the comment is still applied once and
    nothing bad is persisted.
    """
    text = (text or "").strip()
    if not text:
        return _fallback()

    if _EDIT_HINT_RE.search(text):
        return _fallback()

    try:
        return _classify_with_llm(text)
    except Exception:  # noqa: BLE001 -- any LLM/parsing failure is fail-safe
        logger.exception("classify_feedback: LLM classification failed, treating as edit")
        return _fallback()


def _classify_with_llm(text: str) -> Dict[str, Any]:
    from google import genai
    from google.genai import types as genai_types

    from .agent import TEXT_MODEL
    from .quality_rules import CHECKS, MAX_RULE_TEXT

    model = os.environ.get("SLIDEMAKR_CLASSIFY_MODEL") or TEXT_MODEL
    check_ids = sorted(CHECKS.keys())

    prompt = (
        f"User comment after seeing a slide deck: {text}\n\n"
        "Decide: is it a standing preference for all future decks (preference) "
        "or a change to this deck only (edit)? If preference, rewrite it as "
        "one imperative rule and choose check_id from "
        f"{check_ids} or null, with params when the check needs them "
        "(e.g. text_color: {role, hex}; image_present: {only_content_slides: true}; "
        "brand_match: {colors: [...]})."
    )

    schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["preference", "edit"]},
            "rule_text": {"type": "string"},
            "check_id": {"type": "string", "enum": check_ids},
            "params": {"type": "object"},
            "hard": {"type": "boolean"},
        },
        "required": ["kind"],
    }

    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )

    data = json.loads(response.text)
    kind = data.get("kind")
    if kind not in ("preference", "edit"):
        return _fallback()

    if kind == "edit":
        return {"kind": "edit", "rule_text": None, "check_id": None, "params": {}, "hard": False}

    check_id = data.get("check_id") or None
    if check_id not in check_ids:
        check_id = None

    rule_text = (data.get("rule_text") or text).strip()[:MAX_RULE_TEXT].strip()

    return {
        "kind": "preference",
        "rule_text": rule_text,
        "check_id": check_id,
        "params": data.get("params") or {},
        "hard": bool(data.get("hard", False)),
    }
