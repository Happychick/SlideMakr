"""
SlideMakr — speech-to-text via Gemini.

Extracted so both the mobile /generate-audio path and the edit-eval harness use
one STT implementation. Returns the transcript plus wall-clock latency, since
transcription time feeds the eval's speed dimension.
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

_VERBATIM_PROMPT = (
    "Transcribe this audio verbatim. Return ONLY the exact words spoken, "
    "with no commentary, punctuation cleanup, or added text."
)


def transcribe(
    audio_bytes: bytes,
    mime_type: str = "audio/wav",
    prompt: Optional[str] = None,
    model: str = "gemini-2.5-flash",
) -> Tuple[str, float]:
    """Transcribe audio → (text, seconds). Verbatim by default (for WER)."""
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client()
    start = time.time()
    resp = client.models.generate_content(
        model=model,
        contents=[
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                    genai_types.Part.from_text(text=prompt or _VERBATIM_PROMPT),
                ],
            )
        ],
    )
    return (resp.text or "").strip(), round(time.time() - start, 2)
