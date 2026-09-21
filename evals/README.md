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
python -m evals.run --no-vision     # edit-flow: skip the review_slide_layout looks pass
python -m evals.run --final-look    # edit-flow: force run_text_edit(final_look=True)
python -m evals.run --no-final-look # edit-flow: force run_text_edit(final_look=False)
```

`--final-look`/`--no-final-look` default to the `SLIDEMAKR_FINAL_LOOK` env var
(off) when neither flag is passed — see "One final look" below.

**The budget rule:** every run prints a plan — `PLAN: decks=<n> llm_turns=<n>
budget=<N>` — *before* creating anything, computed before any `.env`/client
import or network call. If the planned deck count exceeds `--budget-decks`
(default **1**), the run exits with an error and creates nothing. Raise the
cap explicitly with `--budget-decks N`, or pass `--deck <id>` to reuse an
existing deck (0 planned decks). This replaced an earlier eval shape that
could spin up 50+ decks in one run with no cost control. `--deck` is meant
for a single one-off run against a deck you already have open — don't use it
with `--repeat N` across sessions to build a trend, since edits accumulate
on that same deck instead of starting from the same known-clean state.

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
chart — each scored right after it lands. No TTS/STT, no per-case duplicate
decks: exactly one deck per run (zero with `--deck`).

- Score per edit = **landed × usability × speed**, where speed =
  `min(1, 20 / seconds)`. `landed`/`usability`/`missing` come from the SAME
  judge the live agent runs against (`app/edit_assess.assess`), including a
  `delete` requirement kind (`delete|remove the chart/image/flowchart on
  slide N`, scored by absence of that element). As of 2026-09-21, usability
  is the **product** of its five parts (font consistency × balance × fit ×
  overlap × title), not their mean — one hard layout failure sinks the score.
  `title` is 1.0 when a title is found and **0.7** (not 0.0) when it's
  missing — a missing title is a style nit, not breakage, so it dents the
  product instead of zeroing it out.
- **Vision (on by default, `--no-vision` to skip):** one `review_slide_layout`
  call per edit, against the slide it targeted, folded into that edit's
  usability as one more product term — `overall_quality` mapped good 1.0 /
  needs_fixes 0.6 / poor 0.3 / unknown (or a failed call) 0.5. A `vision X:
  "<reviewer's one-line summary>"` reason line is appended per edit.
- Every sub-score ships a plain-English `reasons` line (what was expected vs.
  what's actually on the slide, the usability sub-score breakdown, the vision
  verdict, the speed vs. target) — printed under each table row and kept in
  the JSON. When the edit runner's geometry self-check ran, a `geometry
  before X → after Y` reason line (the minimum overlap part across the
  edit's touched slides) is appended too; when `run_text_edit`'s
  `final_look` ran, a `final_look ran=<bool> changed=<bool>` line is appended
  last.

### One final look (`SLIDEMAKR_FINAL_LOOK`, Task 1.7)
`app.edit_runner.run_text_edit(..., final_look=True)` runs the existing
see-and-fix loop (Phase B) exactly ONCE after Phase A finishes — regardless
of `SLIDEMAKR_VISION_SELFCHECK`, which still governs the legacy per-turn
loop — capped at one vision pass for this path. Defaults to the
`SLIDEMAKR_FINAL_LOOK` env var (off) when the caller passes `None`. Each
edit's `final_look_ran`/`final_look_changed` telemetry is surfaced in the
edit-flow eval's reasons (see above). The one-deck A/B experiment (final_look
off vs. on, compared on avg_overall and total seconds) needs the owner's
explicit go-ahead before it's run for real — not yet run as of this writing;
results will be added here under "Baseline (2026-09)" once it is.

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
brand-aware (checks the deck actually uses the requested palette). Any non-text element (image, or a shape/line/table with no text of its own, e.g. a flowchart node) covering more than 10% of a text box's area is a hard-capped legibility
failure (`overlap_score` ≤ 0.3 for that slide), named explicitly by
`layout_quality.overlap_details` — it can't be averaged away by an otherwise
tidy deck. This cap was added 2026-09-16: creation-eval `visual_quality`
scores from before that date are not directly comparable to runs after it
for decks with image/text overlap. Edit scores are
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
