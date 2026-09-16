# SlideMakr evals

The eval is the yardstick for the goal: **make the slides the user asked for, in
the least time.** Every change is measured here, not guessed (the "autoverse"
principle — deploy the strategy that scores best).

## Run it

```bash
source slidemakr-venv/bin/activate
python -m evals.run                 # edit-flow (default): 1 deck, 6 scripted edits
python -m evals.run --deck <id>     # reuse a deck instead of creating one
python -m evals.run --creation      # creation only (5+2 prompts)
python -m evals.run --both          # creation + legacy per-case edit eval
python -m evals.run --edit --edit-variants clean,noise,interrupt
```

**The budget rule:** every run prints a plan — `PLAN: decks=<n> llm_turns=<n>
budget=<N>` — *before* creating anything, computed before any `.env`/client
import or network call. If the planned deck count exceeds `--budget-decks`
(default **1**), the run exits with an error and creates nothing. Raise the
cap explicitly with `--budget-decks N`, or pass `--deck <id>` to reuse an
existing deck (0 planned decks). This replaced an earlier eval shape that
could spin up 50+ decks in one run with no cost control.

Each run writes a timestamped JSON report to `results/edit_flow/`,
`results/creation/`, or `results/edit/` (gitignored) so runs are comparable
over time, and prints each case's score **plus a link to the generated deck**.
The `/admin/run-eval` HTTP endpoint runs the same creation eval.

Set `EVAL_SHARE_EMAIL` (in `app/.env`) to auto-share every generated deck with that
address so the links open (they appear under "Shared with me").

Pass `--repeat N` (default 1) to run each selected eval N times — scores are noisy
run-to-run. Each run is still written individually; with `N > 1` a
`results/<kind>/<timestamp>_aggregate_n<N>.json` is also written and a
case | mean | min | max | stdev table is printed. `--repeat` multiplies the
planned deck count too, so it's still gated by `--budget-decks`.

## What the evals are

### Edit-flow eval (`app/edit_flow_eval.py`) — the default
One deck, created from a single 4-slide "remote work" prompt, then a FIXED
script of six text edits applied to that SAME deck in order — retitle,
recolor, add a chart, add a flowchart, retitle the subtitle, then delete the
chart — each scored right after it lands. No TTS/STT, no vision, no per-case
duplicate decks: exactly one deck per run (zero with `--deck`).

- Score per edit = **landed × usability × speed**, where speed =
  `min(1, 20 / seconds)`. `landed`/`usability`/`missing` come from the SAME
  judge the live agent runs against (`app/edit_assess.assess`), including a
  `delete` requirement kind (`delete|remove the chart/image/flowchart on
  slide N`, scored by absence of that element).
- Every sub-score ships a plain-English `reasons` line (what was expected vs.
  what's actually on the slide, the usability sub-score breakdown, the speed
  vs. target) — printed under each table row and kept in the JSON.

### Creation eval (`app/eval.py`)
Generates real decks from standard prompts and scores each on a composite:
**instruction_adherence (35%) · speed (20%) · completeness · api_success ·
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
brand-aware (checks the deck actually uses the requested palette). An image
covering more than 10% of a text box's area is a hard-capped legibility
failure (`overlap_score` ≤ 0.3 for that slide), named explicitly by
`layout_quality.overlap_details` — it can't be averaged away by an otherwise
tidy deck. Edit scores are
noisy run-to-run (the agent is inconsistent) — compare trends across several runs,
not single numbers. `python -m scripts.calibrate_edit_eval` renders the scored
slides next to their scores for human calibration.

## Related tools (not evals)
- `scripts/calibrate_edit_eval.py` — renders slides for human review of edit scores
  (also renders the first slide of each creation-eval deck).
- `scripts/calibration_report.py` — `python -m scripts.calibration_report` builds a
  self-contained `results/calibration/report.html` from the newest aggregate/run
  JSON plus those renders, for at-a-glance score calibration review.
- `scripts/benchmark_batching.py` — a performance benchmark (commit modes), not a
  quality eval.
