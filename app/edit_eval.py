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
