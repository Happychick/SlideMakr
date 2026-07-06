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


def test_check_slide_colors_sees_text_color():
    # recolored bullets carry text_color, not fill_color — must still count.
    st = _state([{"elements": []},
                 {"elements": [{"objectId": "b", "text_color": "#6B46C1", "text": "hi"}]}])
    assert ee.check_slide_colors_on_brand(st, 1, ["#6B46C1"]) == 1.0


# ---------------------------------------------------------------------------
# Usability + composite (accuracy × speed; accuracy = instruction × usability)
# ---------------------------------------------------------------------------

def test_usability_high_for_clean_titled_centered_slide():
    st = _state([{"elements": [
        {"placeholder": "TITLE", "text": "Q4 Board Review", "font": "Georgia",
         "size": {"width": {"magnitude": 3_000_000}, "height": {"magnitude": 800_000}},
         "transform": {"translateX": 3_072_000, "translateY": 2_171_750, "scaleX": 1, "scaleY": 1}},
    ]}])
    assert ee.usability(st, 0) >= 0.85


def test_usability_low_for_offcenter_mixed_font_untitled_slide():
    # flowchart-style: nodes bunched right, different font, no title
    nodes = [
        {"objectId": "node_a", "font": "Arial", "text": "Plan",
         "size": {"width": {"magnitude": 1_000_000}, "height": {"magnitude": 500_000}},
         "transform": {"translateX": 7_500_000, "translateY": 300_000, "scaleX": 1, "scaleY": 1}},
        {"objectId": "node_b", "font": "Arial", "text": "Build",
         "size": {"width": {"magnitude": 1_000_000}, "height": {"magnitude": 500_000}},
         "transform": {"translateX": 7_500_000, "translateY": 1_500_000, "scaleX": 1, "scaleY": 1}},
    ]
    st = _state([{"elements": [{"font": "Georgia", "text": "Original Title"}]},
                 {"elements": []},
                 {"elements": nodes}])
    assert ee.usability(st, 2) < 0.6


def test_edit_score_fast_but_wrong_is_low():
    # instruction not followed → accuracy 0 → overall ~0 even if fast/clean
    r = ee.edit_score(instruction_followed=0.0, usability=1.0, speed=1.0,
                      transcription=1.0, error_rate=1.0)
    assert r["overall"] < 0.1


def test_edit_score_slow_but_perfect_is_capped_by_speed():
    r = ee.edit_score(instruction_followed=1.0, usability=1.0, speed=0.5,
                      transcription=1.0, error_rate=1.0)
    assert 0.4 <= r["overall"] <= 0.6


def test_vision_score_maps_review_quality():
    # finer than the 3 buckets: issue count sharpens the score
    assert ee._vision_score({"assessment": {"overall_quality": "good", "issues": []}}) == 1.0
    assert ee._vision_score({"assessment": {"overall_quality": "needs_fixes", "issues": []}}) == 0.65
    # more issues → lower (a slide with 3 flagged problems is worse)
    assert ee._vision_score({"assessment": {"overall_quality": "needs_fixes",
                                            "issues": [1, 2, 3]}}) == 0.35
    assert ee._vision_score({"assessment": {"overall_quality": "poor", "issues": []}}) == 0.3
    # can't review (error / missing) → neutral, don't punish
    assert ee._vision_score({"status": "error"}) == 0.5
    assert ee._vision_score({}) == 0.5


def test_edit_score_bad_usability_drags_down():
    r = ee.edit_score(instruction_followed=1.0, usability=0.4, speed=1.0,
                      transcription=1.0, error_rate=1.0)
    assert 0.3 <= r["overall"] <= 0.5
