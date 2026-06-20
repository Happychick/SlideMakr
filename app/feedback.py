"""Feedback-to-edit helpers for comments and reviewer notes."""

from __future__ import annotations

from typing import Any, Dict, List


def normalize_feedback_items(raw_items: List[Any]) -> List[Dict[str, Any]]:
    """Normalize feedback strings / comment dicts into a stable shape."""
    items = []
    for item in raw_items:
        if isinstance(item, str):
            text = item.strip()
            if text:
                items.append({"slide_number": None, "text": text})
            continue
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("comment") or "").strip()
            if text:
                slide_number = item.get("slide_number")
                items.append({
                    "slide_number": int(slide_number) if slide_number else None,
                    "text": text,
                })
    return items


def build_feedback_edit_prompt(feedback_items: List[Dict[str, Any]]) -> str:
    """Build a silent edit instruction from normalized feedback."""
    lines = [
        "Apply this reviewer feedback to the deck.",
        "Do not ask clarifying questions unless the feedback is impossible to infer.",
        "Preserve slide-specific feedback: if feedback names slide 2, edit slide 2.",
        "",
        "Feedback:",
    ]
    for item in feedback_items:
        slide_number = item.get("slide_number")
        prefix = f"slide {slide_number}: " if slide_number else ""
        lines.append(f"- {prefix}{item['text']}")
    return "\n".join(lines)
