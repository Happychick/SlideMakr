# SlideMakr evals

The eval is the yardstick for the goal: **make the slides the user asked for, in
the least time.** Every change is measured here, not guessed (the "autoverse"
principle — deploy the strategy that scores best).

## Run it

```bash
source slidemakr-venv/bin/activate
python -m evals.run                 # both creation + edit (default)
python -m evals.run --creation      # creation only
python -m evals.run --edit --edit-variants clean,noise,interrupt
```

Each run writes a timestamped JSON report to `results/creation/` or
`results/edit/` (gitignored) so runs are comparable over time. The `/admin/run-eval`
HTTP endpoint runs the same creation eval.

## What the evals are

### Creation eval (`app/eval.py`)
Generates real decks from standard prompts and scores each on a composite:
**instruction_adherence (35%) · speed (20%) · completeness · error_rate ·
visual_quality · content_richness**.

- 5 standard prompts: `simple_4slide`, `flowchart_deck`, `chart_deck`,
  `image_deck`, `branded_deck` (each with an SLA the speed score is measured against).
- 2 first-shot adherence prompts: `flowchart_on_slide_6`, `specific_chart_and_bullets`
  — a deterministic contract (slide count + required element per slide) scored via
  `app/instruction_contract.py`.

### Edit eval (`app/edit_eval.py`)
Measures the full **voice → text → edit** path with no live mic:
`synthesize` (macOS TTS, + noise/interrupt variants) → `transcribe` (Gemini STT,
records latency) → `app/edit_runner.run_text_edit` (gemini-2.5-flash + narrow
edit tools) → score.

- Cases: `retitle`, `recolor_brand`, `vertical_flowchart`.
- Score = **accuracy × speed**, where accuracy = did-the-instruction-land ×
  usability. Usability = mean(deterministic, vision): deterministic = font
  consistency + balance + fit + title (`app/layout_quality.py`); vision = the
  existing `review_slide_layout` (contrast/legibility/polish). transcription (WER)
  + errors are ±10% modifiers.

## Grounding & calibration

Scores are grounded in real slide geometry/colour (not placeholders) and are
brand-aware (checks the deck actually uses the requested palette). Edit scores are
noisy run-to-run (the agent is inconsistent) — compare trends across several runs,
not single numbers. `python -m scripts.calibrate_edit_eval` renders the scored
slides next to their scores for human calibration.

## Related tools (not evals)
- `scripts/calibrate_edit_eval.py` — renders slides for human review of edit scores.
- `scripts/benchmark_batching.py` — a performance benchmark (commit modes), not a
  quality eval.
