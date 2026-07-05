# Voice → Text → Edit Evaluation Harness — Design

**Date:** 2026-06-29
**Branch:** `integrate/adherence-eval`
**Status:** Approved design

## Context

SlideMakr is being reworked goal-first: *fast + accurate slides*, with an eval
metric gating every iteration (the "autoverse" principle). Step 0 built an honest
**creation** metric (adherence + real geometry/color + brand-match). The next
change is the **voice swap** — dropping the native-audio edit model for an
STT → text-model pipeline. But we cannot A/B that swap without an **editing**
eval, and the metric must include the **voice/STT stage**, because transcription
accuracy, noise/interruption robustness, and transcription latency all feed
"fast + accurate."

Note on "why not live voice": the current `/ws` edit flow needs a real-time
microphone PCM stream, which CI has no device for. But the pipeline is fully
evaluable with **pre-recorded/synthesized audio fixtures** fed through STT — no
live mic required.

## Goal

An automated harness that scores the full **voice → text → edit** path end-to-end,
producing a per-case baseline the voice swap (and STT-choice A/Bs) are measured
against.

## Isolated units

| Unit | Responsibility | Depends on |
|---|---|---|
| `word_error_rate(hyp, ref)` | edit-distance transcription accuracy (0-1) | pure |
| `synthesize(text, variant)` → wav bytes | `say`→aiff→`afconvert`→wav; `variant` ∈ {clean, noise, interrupt} via stdlib `wave` + numpy | `say`, `afconvert` |
| `transcribe(audio, mime)` → `(text, seconds)` | Gemini STT, extracted from `/generate-audio` | Gemini |
| edit-runner | transcript → `gemini-2.5-flash` + narrow edit tools + `commit_edits` on a target deck | edit tools |
| `run_edit_case(case)` | orchestrate one case end-to-end + score | all above |

## Flow (per case)

1. Duplicate a seed deck (reuse `duplicate_presentation`) for isolation.
2. `synthesize` the instruction audio (per variant).
3. `transcribe` → record transcript, WER vs ground truth, STT latency.
4. Run edit-runner on the transcript against the duplicated deck.
5. Capture after-state (`get_presentation_state`) and score.

## Scoring (per case)

- **transcription:** WER (accuracy), STT latency
- **edit:** adherence to the instruction's contract + layout + brand (reuse
  `instruction_contract`, `layout_quality`, `brand_match_score`)
- **speed:** end-to-end = STT + model + Slides API time
- composite, same shape/weights family as the creation metric

## Cases (~4, each clean + one noisy/interrupted variant)

- retitle slide 1 (text edit)
- recolor slide 2 bullets to a brand color (style + brand)
- add a **vertical** flowchart to slide 3 (orientation + layout)
- resize/reposition an element to fit (layout)

## Components

- `app/edit_eval.py` — cases, orchestration, WER, edit-runner
- `app/transcription.py` — extracted Gemini STT (also lets `/generate-audio` reuse it)
- reuse `app/eval.py`, `app/layout_quality.py`, `app/instruction_contract.py`
- `scripts/run_edit_eval.py` — standalone runner (decoupled from uvicorn)

## Testing

- TDD the pure/deterministic units: `word_error_rate`, `synthesize` variants
  (clean produces valid WAV; interrupt concatenates; noise mixes), contract
  scoring, case orchestration with mocked STT + edit-runner.
- The live run is the integration test.

## Non-goals (YAGNI)

- No Deepgram/Orpheus integration yet — Gemini STT first; STT choice becomes an
  autoverse variable later.
- No live-mic testing.
- No TTS narration ("silent mode" is the product direction, not part of the eval).
