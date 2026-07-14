"""
SlideMakr — voice → text → edit evaluation harness.

Scores the full editing path end-to-end: an instruction is synthesized to audio,
transcribed by STT, run through the text edit pipeline, and the resulting deck is
scored with the same adherence/layout/brand metric used for creation. Produces a
per-case baseline the voice swap (and STT-choice A/Bs) are measured against.

See docs/superpowers/specs/2026-06-29-voice-edit-eval-design.md.

This module starts with the pure/deterministic units (word error rate); the
audio, STT, and edit-runner stages are layered on with their own tests.
"""

from __future__ import annotations

import io
import re
import subprocess
import tempfile
import wave
from typing import List, Tuple

import numpy as np


_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12",
}


def _normalize_words(text: str) -> List[str]:
    """Lowercase, strip punctuation, map number-words to digits, split."""
    cleaned = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return [_NUMBER_WORDS.get(w, w) for w in cleaned.split()]


def _edit_distance(a: List[str], b: List[str]) -> int:
    """Levenshtein distance between two token lists (word-level)."""
    m, n = len(a), len(b)
    if m == 0:
        return n
    if n == 0:
        return m
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[n]


def word_error_rate(hypothesis: str, reference: str) -> float:
    """Transcription accuracy as 1 - WER, clamped to [0, 1] (1.0 = perfect).

    WER = word-level edit distance / reference word count. Case- and
    punctuation-insensitive. An empty reference scores 1.0 only if the
    hypothesis is also empty.
    """
    ref = _normalize_words(reference)
    hyp = _normalize_words(hypothesis)
    if not ref:
        return 1.0 if not hyp else 0.0
    distance = _edit_distance(hyp, ref)
    return round(max(0.0, 1.0 - distance / len(ref)), 4)


# ---------------------------------------------------------------------------
# Audio synthesis — fixtures for the voice stage (no live mic needed)
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000


def _read_wav(wav_bytes: bytes) -> Tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype=np.int16), rate


def _write_wav(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    return buf.getvalue()


def _concat_wavs(a: bytes, b: bytes) -> bytes:
    """Concatenate two mono 16-bit WAVs (used to splice in an interruption)."""
    sa, ra = _read_wav(a)
    sb, _ = _read_wav(b)
    return _write_wav(np.concatenate([sa, sb]), ra)


def _mix_noise(wav_bytes: bytes, level: float) -> bytes:
    """Add white noise at `level` (0-1) of full scale; length preserved."""
    if level <= 0:
        return wav_bytes
    samples, rate = _read_wav(wav_bytes)
    # Deterministic noise (seeded) so runs are reproducible.
    rng = np.random.default_rng(0)
    noise = rng.integers(-1, 2, size=samples.shape) * int(level * 8000)
    mixed = np.clip(samples.astype(np.int32) + noise, -32768, 32767)
    return _write_wav(mixed, rate)


def _say_to_wav(text: str) -> bytes:
    """macOS `say` → AIFF → 16kHz mono WAV via `afconvert`."""
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as aiff, \
            tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav:
        subprocess.run(["say", "-o", aiff.name, text], check=True)
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLE_RATE}", "-c", "1",
             aiff.name, wav.name],
            check=True,
        )
        with open(wav.name, "rb") as f:
            return f.read()


def _slide(state: dict, index: int) -> dict:
    slides = (state or {}).get("slides", [])
    return slides[index] if 0 <= index < len(slides) else {}


def check_title_text(state: dict, slide_index: int, expected_substring: str) -> float:
    """1.0 if the slide's title (or any short text) contains the expected text."""
    want = expected_substring.lower().strip()
    for el in _slide(state, slide_index).get("elements", []):
        text = str(el.get("text", "")).lower()
        if want in text:
            return 1.0
    return 0.0


def check_vertical_flowchart(state: dict, slide_index: int) -> float:
    """1.0 if the slide has a vertical, on-page flowchart; else 0.0.

    Combines presence (node_ shapes), orientation, and fit — the four checks
    from the "build a vertical flowchart" example minus subjective aesthetics.
    """
    from . import layout_quality as lq
    els = _slide(state, slide_index).get("elements", [])
    nodes = [e for e in els if str(e.get("objectId", "")).startswith("node_")]
    if not nodes:
        return 0.0
    oriented = lq.flowchart_orientation(els) == "vertical"
    fits = lq.fits_page_score(els) >= 0.99
    return 1.0 if (oriented and fits) else 0.5


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


def usability(state: dict, slide_index: int) -> float:
    """Deterministic slide quality (0-1): font consistency, balance, fit, title.

    No vision — these are the "the agent should just build it right" checks:
    matching font, centered content, no overflow/overlap, a title/label.
    """
    from . import layout_quality as lq
    els = _slide(state, slide_index).get("elements", [])
    font = lq.font_consistency_score(state)
    balance = lq.balance_score(els)
    layout = (lq.fits_page_score(els) + lq.overlap_score(els)) / 2 if els else 1.0
    titled = check_has_title(state, slide_index)
    return round((font + balance + layout + titled) / 4, 4)


def _vision_score(review: dict) -> float:
    """Map review_slide_layout's overall_quality to 0-1 (neutral 0.5 if no review).

    review_slide_layout returns {status, assessment: {overall_quality, ...}}, so
    check the nested assessment; also accept a flat dict for convenience.
    """
    review = review or {}
    assessment = review.get("assessment")
    if not isinstance(assessment, dict):
        assessment = review
    base = {"good": 1.0, "needs_fixes": 0.65, "poor": 0.3}.get(
        assessment.get("overall_quality"), 0.5
    )
    # Sharpen the coarse bucket with the count of flagged issues.
    n_issues = len(assessment.get("issues", []) or [])
    return round(max(0.15, base - 0.1 * n_issues), 4)


def edit_score(
    instruction_followed: float,
    usability: float,
    speed: float,
    transcription: float,
    error_rate: float,
) -> dict:
    """Composite edit score — two gating pillars: accuracy × speed.

    accuracy = did-the-instruction-land × usability, so a fast slide that ignored
    the instruction, or is unusable, scores low. transcription/errors are small
    modifiers (±10%), not props.
    """
    accuracy = instruction_followed * usability
    modifier = 0.9 + 0.1 * min(transcription, error_rate)
    overall = round(accuracy * speed * modifier, 4)
    return {"accuracy": round(accuracy, 4), "overall": overall}


def synthesize(text: str, variant: str = "clean") -> bytes:
    """Synthesize an instruction to 16kHz mono WAV bytes.

    variant:
      - "clean": the instruction, spoken plainly
      - "noise": clean + white background noise
      - "interrupt": an unrelated aside spliced in before the instruction
    """
    speech = _say_to_wav(text)
    if variant == "noise":
        return _mix_noise(speech, level=0.3)
    if variant == "interrupt":
        aside = _say_to_wav("hang on, what time is it")
        return _concat_wavs(aside, speech)
    return speech


# ---------------------------------------------------------------------------
# Text edit-runner — the shared voice-swap engine lives in app/edit_runner.py.
# ---------------------------------------------------------------------------

from .edit_runner import run_text_edit  # noqa: E402  (single source)


# ---------------------------------------------------------------------------
# Edit cases + end-to-end orchestration
# ---------------------------------------------------------------------------

def _verify_retitle(after: dict, ctx: dict) -> float:
    return check_title_text(after, 0, "q4 board review")


def _verify_recolor(after: dict, ctx: dict) -> float:
    return check_slide_colors_on_brand(after, 1, ctx.get("brand_colors", []))


def _verify_flowchart(after: dict, ctx: dict) -> float:
    return check_vertical_flowchart(after, 2)


EDIT_CASES = [
    {"id": "retitle", "instruction": "change the title of slide 1 to Q4 Board Review",
     "verify": _verify_retitle, "slide_index": 0, "sla_seconds": 30},
    {"id": "recolor_brand", "instruction": "recolor the bullets on slide 2 to Stripe purple",
     "verify": _verify_recolor, "brand": "Stripe", "slide_index": 1, "sla_seconds": 35},
    {"id": "vertical_flowchart",
     "instruction": "add a vertical flowchart to slide 3 showing plan then build then ship",
     "verify": _verify_flowchart, "slide_index": 2, "sla_seconds": 40},
]


async def _create_seed_deck() -> str:
    """Create a deterministic-ish 3-slide seed via the creation agent."""
    from google.genai import types
    from . import server  # lazy — avoids circular import at module load
    session = await server.session_service.create_session(
        app_name=server.APP_NAME, user_id="edit_eval_seed"
    )
    prompt = (
        "Create a 3-slide presentation. Slide 1: a title slide titled 'Original Title'. "
        "Slide 2: a slide titled 'Details' with three bullet points about coffee. "
        "Slide 3: a blank slide."
    )
    content = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    pid = ""
    async for event in server.text_runner.run_async(
        user_id="edit_eval_seed", session_id=session.id, new_message=content
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.function_response:
                    resp = part.function_response.response
                    if isinstance(resp, dict) and resp.get("presentation_id"):
                        pid = resp["presentation_id"]
    return pid


async def run_edit_case(case: dict, seed_pid: str, variant: str = "clean") -> dict:
    from . import slidemakr, transcription
    from .eval import score_speed, score_error_rate, _brand_palette

    dup = slidemakr.duplicate_presentation(seed_pid, f"edit-eval-{case['id']}-{variant}")
    pid = dup["presentation_id"]

    ctx = {"brand_colors": _brand_palette(case["brand"]) if case.get("brand") else []}

    # voice → text
    wav = synthesize(case["instruction"], variant)
    transcript, stt_s = transcription.transcribe(wav, "audio/wav")
    wer = word_error_rate(transcript, case["instruction"])

    # text → edit
    edit = await run_text_edit(pid, transcript)
    after = slidemakr.get_presentation_state(pid)

    # score — two pillars: accuracy (instruction × usability) × speed
    idx = case["slide_index"]
    edit_correct = case["verify"](after, ctx)
    det_usab = usability(after, idx)
    # Blend in the existing vision reviewer for the subjective last mile
    # (contrast/legibility/polish) the deterministic checks can't see.
    slide_id = after["slides"][idx]["slide_id"]
    try:
        from .agent import review_slide_layout
        vision = _vision_score(review_slide_layout(pid, slide_id))
    except Exception as e:  # noqa: BLE001 — vision is best-effort
        logger = __import__("logging").getLogger(__name__)
        logger.warning(f"vision review failed: {e}")
        vision = 0.5
    usab = round((det_usab + vision) / 2, 4)
    total_s = round(stt_s + edit["duration_seconds"], 2)
    speed = score_speed(total_s, case["sla_seconds"])
    err = score_error_rate(edit["success_count"], edit["total_requests"] or 1)
    scored = edit_score(edit_correct, usab, speed, wer, err)
    return {
        "id": case["id"], "variant": variant, "presentation_id": pid,
        "transcript": transcript, "committed": edit["committed"],
        "scores": {
            "edit_correct": edit_correct, "usability": usab,
            "usability_deterministic": det_usab, "usability_vision": vision,
            "accuracy": scored["accuracy"], "transcription": wer,
            "speed": round(speed, 4), "error_rate": round(err, 4),
        },
        "stt_seconds": stt_s, "edit_seconds": edit["duration_seconds"],
        "total_seconds": total_s, "overall": scored["overall"],
    }


async def run_edit_eval(variants=("clean",)) -> dict:
    seed = await _create_seed_deck()
    if not seed:
        return {"error": "seed creation failed"}
    results = []
    for case in EDIT_CASES:
        for variant in variants:
            try:
                results.append(await run_edit_case(case, seed, variant))
            except Exception as e:  # noqa: BLE001 — isolate case failures
                results.append({"id": case["id"], "variant": variant, "error": str(e)})
    ok = [r for r in results if "overall" in r]
    avg = round(sum(r["overall"] for r in ok) / len(ok), 4) if ok else 0.0
    return {"seed_presentation_id": seed, "avg_overall": avg, "results": results}
