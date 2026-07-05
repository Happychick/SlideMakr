"""
Tests for app/edit_eval.py — the voice → text → edit evaluation harness.

Pure/deterministic units are unit-tested here; the live end-to-end run (real TTS,
Gemini STT, edit agent, Slides API) is the integration test run via
scripts/run_edit_eval.py.
"""

from __future__ import annotations

from app import edit_eval as ee


# ---------------------------------------------------------------------------
# Word error rate — transcription accuracy (0-1, 1.0 = perfect)
# ---------------------------------------------------------------------------

def test_wer_perfect_match_scores_one():
    assert ee.word_error_rate("change the title to Q4", "change the title to Q4") == 1.0


def test_wer_is_case_and_punctuation_insensitive():
    assert ee.word_error_rate("Change the Title to Q4.", "change the title to q4") == 1.0


def test_wer_one_substitution():
    # 1 wrong word out of 5 → 80% correct
    assert ee.word_error_rate("change the header to Q4", "change the title to Q4") == 0.8


def test_wer_deletion_penalised():
    # dropped a word: 4 of 5 present
    assert ee.word_error_rate("change title to Q4", "change the title to Q4") == 0.8


def test_wer_totally_wrong_scores_zero():
    assert ee.word_error_rate("what time is it", "change the title to Q4 board review") == 0.0


def test_wer_empty_hypothesis_scores_zero():
    assert ee.word_error_rate("", "change the title") == 0.0


def test_wer_normalizes_number_words_to_digits():
    # "slide one" vs "slide 1" is not a transcription error.
    assert ee.word_error_rate("change slide one to Q4", "change slide 1 to Q4") == 1.0


# ---------------------------------------------------------------------------
# WAV ops — deterministic audio manipulation (isolated from the `say` shell-out)
# ---------------------------------------------------------------------------

import io  # noqa: E402
import wave  # noqa: E402
import struct  # noqa: E402


def _wav_bytes(n_frames: int, rate: int = 16000, value: int = 1000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<" + "h" * n_frames, *([value] * n_frames)))
    return buf.getvalue()


def _frames(wav: bytes) -> int:
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes()


def test_concat_wavs_sums_frames():
    out = ee._concat_wavs(_wav_bytes(100), _wav_bytes(50))
    assert _frames(out) == 150


def test_mix_noise_preserves_length_but_changes_samples():
    clean = _wav_bytes(200, value=1000)
    noisy = ee._mix_noise(clean, level=0.3)
    assert _frames(noisy) == 200
    assert noisy != clean


def test_mix_noise_zero_level_is_unchanged():
    clean = _wav_bytes(200, value=1000)
    assert ee._mix_noise(clean, level=0.0) == clean


# ---------------------------------------------------------------------------
# Edit-specific verification (a generic contract can't check "title now says X")
# ---------------------------------------------------------------------------

def _state(slides):
    return {"slide_count": len(slides), "slides": slides}


def test_check_title_text_matches():
    st = _state([{"elements": [{"placeholder": "TITLE", "text": "Q4 Board Review"}]}])
    assert ee.check_title_text(st, 0, "q4 board review") == 1.0


def test_check_title_text_absent():
    st = _state([{"elements": [{"placeholder": "TITLE", "text": "Untitled"}]}])
    assert ee.check_title_text(st, 0, "q4 board review") == 0.0


def test_check_vertical_flowchart_present_and_vertical():
    nodes = [
        {"objectId": "node_a", "type": "shape",
         "size": {"width": {"magnitude": 1_000_000}, "height": {"magnitude": 500_000}},
         "transform": {"translateX": 4_000_000, "translateY": 300_000, "scaleX": 1, "scaleY": 1}},
        {"objectId": "node_b", "type": "shape",
         "size": {"width": {"magnitude": 1_000_000}, "height": {"magnitude": 500_000}},
         "transform": {"translateX": 4_000_000, "translateY": 1_500_000, "scaleX": 1, "scaleY": 1}},
    ]
    st = _state([{"elements": []}, {"elements": []}, {"elements": nodes}])
    assert ee.check_vertical_flowchart(st, 2) == 1.0


def test_check_vertical_flowchart_missing():
    st = _state([{"elements": []}, {"elements": []}, {"elements": []}])
    assert ee.check_vertical_flowchart(st, 2) == 0.0


def test_check_slide_colors_on_brand():
    st = _state([{"elements": []},
                 {"elements": [{"objectId": "b", "fill_color": "#6B46C1"}]}])
    assert ee.check_slide_colors_on_brand(st, 1, ["#6B46C1"]) == 1.0
    assert ee.check_slide_colors_on_brand(st, 1, ["#FF0000"]) == 0.0
