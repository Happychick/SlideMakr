# Growable quality rules: one list the agent follows and the eval scores

## Context

Christina's framing (2026-09-21): the eval measures two fixed things, **did you make the deck I asked for** and **how fast**. The agent considers a pass finished only when instruction following hits 1.0 and the slides look right. The third thing, **looks good**, is not fixed: it starts with universal rules (nothing covers text, one font family, nothing off the page) and grows with each user's feedback ("make headings green and body blue", "always pictures over text", "no title-less slides"). The agent must follow exactly the list the eval scores, so after a week three users of the same product have three different products. Her Codex notes define three personalities to test this with.

Today the "looks right" checks are hardcoded in two places that can drift apart: `app/layout_quality.py` (what the eval scores) and the prose in `app/skills/google_slides.md:126-142` (what the agent is told). There is no per-user layer, and no path from a comment into either place.

## Findings from exploration (read-only, 2026-09-21)

- **Scorers and prompt already mirror each other, by hand.** `app/edit_assess.py:usability_parts` exposes font/balance/fit/overlap/title from `app/layout_quality.py`; `usability()` is their product. The skill prose says the same things in words. Nothing links them.
- **Prompt assembly is import-time.** `app/agent.py:940-950` reads the skill once; `AGENT_INSTRUCTION`/`EDIT_INSTRUCTION` are constants baked into three `Agent(...)` constructions (`agent.py:1073/1083/1218`). Per-user rules need per-request assembly. `app/edit_runner.build_text_edit_agent()` already builds its agent per call, so the text edit path is the safe place to start.
- **A per-user store exists and is unused.** `app/db.py:415-440` `save_user_memory`/`get_user_memory`, collection `user_memory`. No callers. Stable ids only for Google-signed-in users.
- **Feedback plumbing exists on the Codex branch** (`codex/slidemakr-feedback-editing`: `normalize_feedback_items`, `build_feedback_edit_prompt`, `POST /api/comments/apply-feedback`). It turns feedback into a one-off edit and persists nothing.
- **Live self-correction (Task 1.6) already consumes `usability_parts`**, so any rule added there is something the agent is told to fix after a commit.
- **Personalities:** in Christina's Google Doc (Josh Boon, Jenna Dash, Alex Ward); captured below as persona fixtures.

## Design

### The three pillars, and which one grows
| Pillar | Fixed? | Scored by | Agent stops when |
|---|---|---|---|
| Instruction following (`landed`) | fixed | `edit_assess.assess` contract checks | 1.0 |
| Speed | fixed target, must fall over time | seconds vs target, per edit and per step | never blocks a pass; see Speed loop below |
| Looks good (`looks_good`, today's `usability`) | **grows** | `RuleSet.score` (this plan) | every hard rule passes |

### Speed loop (Christina, 2026-09-21): the number must drive fixes, not just get printed
- **Per-step timing in the run dict.** `run_text_edit` already returns `duration_seconds`; extend it with `timeline: [{step: "llm_turn"|"commit"|"state_read"|"render"|"correction", seconds}]` (the ADK events and `commit_edits` already carry timings). The edit-flow eval prints the slowest step per edit under the reasons: "speed 0.53: 37.6s vs 20s target — slowest: llm_turn 29.1s (2 tool calls), commit 4.2s".
- **The agent sees its own time.** The commit result the agent reads after each edit includes `seconds_so_far` and the target; the instruction says: if you are over target, prefer fewer, batched tool calls on the next edit and do not re-read state you already have. Cheap, no extra turn.
- **The dev loop sees it too.** `evals/run.py` writes `results/speed/<ts>.json` with the slowest steps across the run; the README "Baseline" section tracks the per-edit median week over week. When a step is consistently the slowest, that is the next engineering task (e.g. chart image upload, template state read). Target starts at 20s per edit and is lowered in the README when the median beats it for a week.
- Speed never blocks a pass (a slow correct deck is still delivered), but it is the third number on every row and it must trend down.

### Rule model (`app/quality_rules.py`, new)
```python
@dataclass(frozen=True)
class Rule:
    id: str            # "no_overlap" | "user:headings_green"
    text: str          # imperative prompt line
    scope: str         # "universal" | "user"
    check_id: str|None # key into CHECKS: state-based scorer, or None = prompt + vision only
    params: dict       # e.g. {"color_hex": "#008000", "target": "headings"}
    hard: bool         # hard = agent must fix before it declares done
    source: str        # "default" | "feedback:<iso ts>"
```
- `CHECKS` registry: `overlap`, `fit`, `one_font`, `balanced`, `has_title`, `text_color_matches(params)`, `image_present(params)` (all from existing `layout_quality` and `edit_assess` functions; two new small scorers for colour-of-role and picture-over-text).
- Defaults (universal, hard): `no_overlap`, `fits_page`, `one_font`; universal soft: `balanced`, `charts_titled` (prompt-only).
- Persisted user rules are data (`check_id` + `params`), never code. Feedback that has no scorer becomes a prompt-only rule and is judged by the final look (Task 1.7 vision pass).

### One source of truth
- `RuleSet.render_for_prompt()` → a "Quality rules — you are scored on exactly these" block: universal first, then "This user's preferences". Replaces the hand-written Anti-patterns/Quality bar prose in `app/skills/google_slides.md:126-142`.
- `RuleSet.score(state, idx)` → `{"universal": {id: 0..1}, "user": {id: 0..1}}`; `looks_good_baseline` = product of universal, `looks_good_user` = product of user rules (JSON keys renamed from `usability_*`; `usability()` stays as an alias for old tests). **Two numbers**, so the baseline stays comparable across users and the second shows adaptation.
- `edit_assess.usability_parts` delegates to the default RuleSet (existing tests keep passing). `edit_runner` Phase A corrective message appends unmet user rules by text. `edit_flow_eval` reports both usability numbers and one reason line per rule.
- Instruction assembly: `agent.build_instruction(preamble, rules)`; module-level agents keep defaults; `edit_runner.build_text_edit_agent(rules)` and `run_text_edit(..., rules)` take the per-user set; `server._apply_text_edit` loads rules by user id. Voice `/ws` agents stay on defaults until this proves out.

### Feedback → rule
- `POST /api/feedback {presentation_id, text}`: regex fast path (`slide \d+`, "this", "here" → one-off edit); otherwise one classification call (model id from env, never literal) returning `{kind: preference|edit, rule_text, check_id, params}`. Preference → `db.add_user_rule` **and** applied to the current deck now. Response carries `saved_rule`.
- Frontend: one comment box under the preview; on `saved_rule` show "Saved as your preference: … [undo]" → `DELETE /api/rules/{id}`.
- Storage: `db.get_user_rules/add_user_rule/delete_user_rule` on `user_rules/{user_id}`, in-memory fallback. Guests: `localStorage` id sent in the body, accepted only without a Google session; merged on sign-in (later).

### Eval: proving personalisation
- `evals/run.py --rules-json <file>` (and `--rules-user <id>`): runs the one-deck edit flow **as that user**. Three fixture files under `evals/personas/` from Christina's three personalities. Same instruction script, three decks, three visibly different results; `usability_user` per persona is the number that must rise when the prompt carries the rules and not when it doesn't (`--no-prompt-rules` control).
- Budget rule unchanged: 1 deck per run; the persona comparison is 3 runs, run only on "go".

## Ordered tasks (each TDD, reviewed, one commit)
1. `app/quality_rules.py` + `tests/test_quality_rules.py`: Rule/RuleSet/CHECKS/defaults; `score` reproduces today's `usability_parts` on fixtures; `render_for_prompt` lists rule texts. `edit_assess.usability_parts` delegates; existing tests green.
2. `db.py` rule CRUD (+ in-memory fallback) + tests.
3. `agent.build_instruction` + `edit_runner` per-user rules + corrective message names unmet user rules; test: instruction contains the user rule text; unmet user rule appears in the corrective turn. Move the skill's Anti-patterns/Quality bar prose into rule texts.
4. `edit_flow_eval` two looks-good numbers + per-rule reasons + per-step `timeline` and slowest-step reason; `results/speed/` output; `evals/run.py --rules-json/--rules-user/--no-prompt-rules`; test: a failing user rule lowers `usability_user` only.
5. `app/feedback.py` classify (regex path tested for real, LLM mocked) + `POST /api/feedback`, `DELETE /api/rules/{id}` + tests.
6. Frontend comment box + saved-preference line + guest id.
7. `evals/personas/*.json` from the three personalities; README section; then the 3-run persona comparison (on "go").

Risky refactor: per-request instruction assembly. Mitigation: only the text-edit path (`edit_runner`) gets per-user prompts in this plan; module-level agents untouched.

## Verification
- `pytest tests/ -q` green at every task (baseline 210 + Task 1.6 fix).
- Task 3: unit test asserts the rendered instruction contains a user rule's text and that the corrective message names an unmet user rule.
- Task 4: `python -m evals.run --rules-json evals/personas/a.json` prints `usability_baseline` and `usability_user` per edit with a reason per rule; `--no-prompt-rules` prints lower `usability_user` on the same deck (control).
- Task 7: three runs, three decks, screenshots of the same slide across personas showing the differences; Christina judges by eye.

## The three personalities (from Christina's Google Doc, 2026-09-21) → `evals/personas/*.json`

Each persona = starting rules (what they'd say after seeing a first deck) + the feedback they'd give during the week. The eval runs the same six-edit script as each persona; the decks must diverge.

| Persona | Who | Starting user rules (check_id → params) | Week-1 feedback that becomes rules |
|---|---|---|---|
| **Josh Boon** | Management consultant, high achiever, answers his boss's comments at 2am | `every_slide_has_title` (has_title, hard); `action_titles` (prompt-only: title states the takeaway as a full sentence); `dense_bullets_ok` (prompt-only: up to 6 bullets, 16pt) ; `source_line` (prompt-only: a footnote line on data slides) | "Charts need a source", "Make the takeaway the title", "Numbers bold" (text_style, params {bold: true, target: numbers}) |
| **Jenna Dash** | VP at a VC, travels, sells the fund, must use the company template, wants zero time spent | `template_only` (hard: never move or restyle template placeholders; check: placeholder transforms unchanged vs template) ; `brand_palette` (brand_match, params {colors: template palette}); `one_idea_per_slide` (prompt-only: ≤3 bullets) | "Use our template, don't touch fonts", "Fewer words, bigger", "Every deck ends with a contact slide" (prompt-only) |
| **Alex Ward** | Marketing director, rarely makes slides, narrative first, fixes the last 20% by hand | `pictures_over_text` (image_present, hard on content slides); `narrative_flow` (prompt-only: each title continues the story of the previous one); `short_text` (prompt-only: ≤12 words per slide body) | "More images, less text", "Headings green, body dark blue" (text_color, params {headings: #008000, body: #0B2A6F}), "No flowcharts, use a picture" |

Fixture shape (`evals/personas/josh_boon.json`): `{"user_id": "persona:josh_boon", "rules": [{"id": "user:every_slide_has_title", "text": "...", "check_id": "has_title", "params": {}, "hard": true}], "feedback": ["Charts need a source", ...]}`. The eval applies `rules` from the start; a `--with-feedback` flag also feeds each `feedback` line through the classifier so the rule-saving path is exercised end to end.

New scorers needed for these personas (Task 1 CHECKS): `has_title`, `image_present`, `text_color(params)`, `text_style(params)`, `brand_match(params)`, `placeholders_untouched` (compare touched placeholders' transforms to the template's). Everything else is prompt-only and judged by the final look.

## Sequencing with current work
Task 1.6 (geometry self-check) has an unfinished fix round in the worktree; finish and review it first, then Task 1.7 (final look), then this plan's tasks 1–7. Stripe (Phase 2) follows.
