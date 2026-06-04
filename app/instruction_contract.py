"""
Instruction contracts for first-shot usable decks.

This module converts loose user language into a small deterministic contract
that the creation agent must satisfy. It also scores finished presentations
against that contract so speed is only celebrated after adherence.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _number_from_text(value: str) -> Optional[int]:
    value = (value or "").strip().lower()
    if value.isdigit():
        return int(value)
    return NUMBER_WORDS.get(value)


def _clean_prompt(prompt: str) -> str:
    text = re.sub(r"\b(um|uh|like|you know|kind of|sort of)\b", " ", prompt, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _extract_slide_count(text: str) -> Optional[int]:
    patterns = [
        r"\b(?:make|create|build|generate)\s+(?:exactly\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)[-\s]+slide",
        r"\bexactly\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+slides?",
        r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)[-\s]+slide\s+(?:deck|presentation)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return _number_from_text(match.group(1))
    return None


def _extract_style(text: str) -> str:
    match = re.search(
        r"\b(?:make it|use|in)\s+([A-Z][A-Za-z0-9&.\-\s]{1,40}\s+style)\b",
        text,
        flags=re.I,
    )
    return match.group(1).strip() if match else ""


def _sentence_window(text: str, start: int, end: int) -> str:
    left = max(text.rfind(".", 0, start), text.rfind(";", 0, start))
    right_candidates = [idx for idx in (text.find(".", end), text.find(";", end)) if idx != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1:right].strip()


def _add_requirement(
    requirements: List[Dict[str, Any]],
    slide_number: int,
    kind: str,
    description: str,
    quantity: Optional[int] = None,
    subtype: str = "",
) -> None:
    req: Dict[str, Any] = {
        "slide_number": slide_number,
        "kind": kind,
        "description": description.strip(),
    }
    if quantity is not None:
        req["quantity"] = quantity
    if subtype:
        req["subtype"] = subtype
    else:
        req["subtype"] = ""
    requirements.append(req)


def build_instruction_contract(prompt: str) -> Dict[str, Any]:
    """Build a structured, deterministic contract from user instructions."""
    cleaned = _clean_prompt(prompt)
    requirements: List[Dict[str, Any]] = []

    slide_pattern = re.compile(
        r"\bslide\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b",
        flags=re.I,
    )
    for match in slide_pattern.finditer(cleaned):
        slide_number = _number_from_text(match.group(1))
        if not slide_number:
            continue
        window = _sentence_window(cleaned, match.start(), match.end())
        lower = window.lower()

        if "flow chart" in lower or "flowchart" in lower:
            _add_requirement(requirements, slide_number, "flowchart", window)
        if "chart" in lower and "flow chart" not in lower and "flowchart" not in lower:
            subtype = ""
            for candidate in ("bar", "line", "pie", "doughnut", "radar"):
                if candidate in lower:
                    subtype = candidate
                    break
            _add_requirement(requirements, slide_number, "chart", window, subtype=subtype)
        if "bullet" in lower:
            qty_match = re.search(
                r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+bullets?\b",
                lower,
            )
            quantity = _number_from_text(qty_match.group(1)) if qty_match else None
            _add_requirement(requirements, slide_number, "bullets", window, quantity=quantity)
        if "closing" in lower or "conclusion" in lower:
            _add_requirement(requirements, slide_number, "closing", window)
        if "image" in lower or "photo" in lower:
            _add_requirement(requirements, slide_number, "image", window)

    return {
        "source_prompt": prompt,
        "cleaned_prompt": cleaned,
        "slide_count": _extract_slide_count(cleaned),
        "style": _extract_style(cleaned),
        "requirements": requirements,
        "unasked_clarification_count": 0,
    }


def build_contract_prompt(prompt: str, contract: Dict[str, Any]) -> str:
    """Wrap the raw prompt with a contract the agent must obey."""
    return (
        "Create this presentation fast, but only count it as successful if every "
        "instruction in the contract is satisfied on the first shot.\n\n"
        "Do not ask clarifying questions unless the request is impossible to infer.\n"
        "Do not move requested elements to a different slide.\n"
        "When the contract names slide 6, slide 6 must contain that element.\n\n"
        "ORIGINAL USER REQUEST:\n"
        f"{prompt}\n\n"
        "INSTRUCTION CONTRACT:\n"
        f"{json.dumps(contract, indent=2)}"
    )


def _slide_has_kind(slide: Dict[str, Any], kind: str, subtype: str = "") -> bool:
    text_blob = " ".join(str(e.get("text", "")) for e in slide.get("elements", [])).lower()
    elements = slide.get("elements", [])
    if kind == "flowchart":
        return any(
            e.get("type") == "shape" and e.get("shapeType") not in ("TEXT_BOX", None)
            for e in elements
        )
    if kind == "chart":
        if subtype and subtype in text_blob and "chart" in text_blob:
            return True
        return any(e.get("type") == "image" for e in elements) or "chart" in text_blob
    if kind == "bullets":
        return any("\n" in str(e.get("text", "")) for e in elements) or "•" in text_blob
    if kind == "closing":
        return any(word in text_blob for word in ("thank", "conclusion", "takeaway", "next step", "closing"))
    if kind == "image":
        return any(e.get("type") == "image" for e in elements)
    return False


def _find_kind_slides(state: Dict[str, Any], kind: str, subtype: str = "") -> List[int]:
    found = []
    for idx, slide in enumerate(state.get("slides", []), start=1):
        if _slide_has_kind(slide, kind, subtype=subtype):
            found.append(idx)
    return found


def score_instruction_adherence(
    contract: Dict[str, Any],
    presentation_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Score a finished deck against an instruction contract."""
    missing: List[str] = []
    wrong_slide: List[str] = []
    checks = 0
    passed = 0

    expected_count = contract.get("slide_count")
    if expected_count:
        checks += 1
        if presentation_state.get("slide_count") == expected_count:
            passed += 1
        else:
            missing.append(f"slide_count:{expected_count}")

    slides = presentation_state.get("slides", [])
    for req in contract.get("requirements", []):
        checks += 1
        slide_number = req["slide_number"]
        kind = req["kind"]
        subtype = req.get("subtype", "")
        target = slides[slide_number - 1] if 0 < slide_number <= len(slides) else {}
        if _slide_has_kind(target, kind, subtype=subtype):
            passed += 1
            continue

        missing.append(f"slide_{slide_number}:{kind}")
        found = _find_kind_slides(presentation_state, kind, subtype=subtype)
        if found:
            wrong_slide.append(f"{kind}_expected_slide_{slide_number}_found_slide_{found[0]}")

    score = 1.0 if checks == 0 else round(passed / checks, 4)
    return {
        "instruction_adherence_score": score,
        "missing_element_errors": missing,
        "wrong_slide_errors": wrong_slide,
        "unasked_clarification_count": contract.get("unasked_clarification_count", 0),
        "checks_passed": passed,
        "checks_total": checks,
    }
