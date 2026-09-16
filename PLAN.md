# SlideMakr — PLAN.md (the execution contract)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship SlideMakr as a paid product: a trustworthy eval that the owner has calibrated by eye, a working Stripe credit gate in production, and the Codex workstreams (trust docs, Slides add-on, PowerPoint add-in, feedback editing, GTM) landed on `main` one at a time. Done = a stranger can buy credits and make a deck at the live URL, and every deploy is gated by the eval.

**Architecture:** One trunk (`main`). The unmerged branch stack (`integrate/adherence-eval` → `chore/repo-cleanup` → `feat/voice-swap` → `feat/read-after-write`) becomes `main` first, because it holds the eval harness and the crash-free STT→text edit pipeline. Every later phase is a small reviewed merge onto that trunk, each gated by `python -m evals.run` and `pytest`. Codex branches are cherry-picked by single commit, never merged wholesale (they were built on a lighter base and conflict).

**Tech stack:** FastAPI, Google ADK, Gemini (`gemini-2.5-flash` for creation and edit, Gemini STT), Google Slides/Drive APIs, Firestore, Stripe hosted Checkout (`stripe>=13`), Cloud Run via `deploy.sh`, pytest. Reasoning models for workers by capability name from `../MODELS.md` (`coding_cheap` default, `coding_frontier` only when a debugger is needed).

**Spec sources (read these, they travel with the plan):** `docs/handoffs/2026-07-14.md` (north star, branch stack), `evals/README.md` (the metric), `PROJECT_PLAN.md` Step 11 (Stripe spec), `<local memory dir>` (integration decision, Jun 19 2026), `docs/selling/sell-block-1.md` (sales block).

**Orchestration:** Claude plans and reviews. Codex executes phase by phase, writes `EXECUTION_REPORT.md` after each phase, and stops at every **GATE** for the owner. The Linear ticket is the long-term memory (see Phase 0, task 0.4). Max 3 execute/review cycles per phase before a human call.

## Global constraints

- Python 3.13 venv at `slidemakr-venv/` (recreate, see 0.1). Run everything as `slidemakr-venv/bin/python -m ...`.
- `python -m pytest tests/ -q` must stay green at every commit (119 tests on the trunk today).
- Public repo: nothing personal, no keys, no `.env`, no `*.json` keys. `config`, `results/`, `.claude/`, `FUTURE.md` stay gitignored.
- Never hardcode a model id in code you add; read it from an env var or the existing constant in `app/agent.py`.
- No changes to `app/auth.py` `force_reauth` logic, and no re-enabling the native-audio `/ws` path as default.
- Every UI change is verified in the browser preview (`.claude/launch.json` → "SlideMakr ADK Server", port 8080) with clean console and server logs, and a screenshot in the execution report. Cards in a row share one CSS grid; layouts stack under 900px.
- Prod deploy only after the phase's GATE is approved by the owner. Deploy: `export PATH=$HOME/google-cloud-sdk/bin:$PATH && ./deploy.sh`.
- Do not fabricate eval numbers. Paste the JSON summary and deck links from `results/` into the report.

---

## Phase 0 — One trunk, working tooling, project memory

**Why first:** three months of work sits on unmerged branches; the newest branch was never pushed; the venv is broken by the folder move; there is no Linear ticket.

### Task 0.1 — Recreate the venv (folder moved from ~/SlideMakr to ~/Product/SlideMakr)
**Files:** none in git. `slidemakr-venv/` (gitignored).
- [ ] `cd <repo>/ && rm -rf slidemakr-venv && python3.13 -m venv slidemakr-venv && slidemakr-venv/bin/pip install -r requirements.txt`
- [ ] Verify: `source slidemakr-venv/bin/activate && python -c "import fastapi, google.adk; print('ok')"` prints `ok`.
- [ ] Delete the stale second `venv/` folder only if `git check-ignore venv` says it is ignored (it is not part of the repo either way).

### Task 0.2 — Back up and land the branch stack on `main`
**Files:** git only.
- [ ] `git push -u origin feat/read-after-write` (it is local-only, 29 commits ahead of main, dated 2026-08-11).
- [ ] On that branch: `slidemakr-venv/bin/python -m pytest tests/ -q` → expect all green. Paste the count into the report.
- [ ] Open a PR `feat/read-after-write` → `main` titled `Land eval harness + STT→text edit pipeline + read-after-write (stack)`. Body: link PR #1 and note it is superseded because the stack is linear and PR #1's commits are contained.
- [ ] Close PR #1 with the comment "Superseded by #<new>" (do not delete `chore/repo-cleanup`).
- [ ] **Do not merge yet.** Merge happens in Task 1.5 after the eval gate.

### Task 0.2b — Fix the three merge blockers found in review (2026-09-16)
**Files:** Modify `app/edit_assess.py`, `app/edit_runner.py`, Test `tests/test_edit_assess.py`, `tests/test_edit_controller.py`.
Review verdict on `feat/read-after-write`: SHIP WITH NOTES, 142 tests green. Fix before merge:
- [ ] **Retitle parser over-fires** (`edit_assess.py:198-206`). Failing tests: `"move the title to the left"` → not checkable; `"change the title color on slide 2 to blue"` → not a retitle; `"change the subtitle of slide 1 to Welcome to Acme"` → value `"Welcome to Acme"`. Fix: require a verb `\b(change|set|rename|retitle|update)\b` before `title`, split on the FIRST ` to ` after "title", reject values that are colour words or start with "the ".
- [ ] **Assessment exception can 500 a successful edit** (`edit_runner.py` around the two `assess(...)` calls). Failing test: `assess` raises → `run_text_edit` still returns with `checkable=False`. Fix: wrap both calls in `try/except Exception` and log.
- [ ] **Recolor to a plain colour triggers a web brand lookup** (`edit_assess.py:_resolve_brand_colors`). Failing test: brand `"blue"` with `ctx=None` → no call to `app.eval._brand_palette`. Fix: if brand is in `_COLOR_WORDS`, return the hex for that colour word from a small dict instead of searching.
- [ ] Optional cleanups from the same review: replace the hand-coded `_verify_*` in `edit_eval.py:208-217` with `assess(...)["landed"]`; drop the "text mentions chart" fallback in `check_has_chart`; key `touched` by `str` before returning it over HTTP.
- [ ] Tests green. Commit `fix: harden edit self-correction (parser, exception guard, no web lookup for colour words)`.

### Task 0.3 — Retire the stale planning docs
**Files:** Modify `PROJECT_PLAN.md` (move), `HANDOFF.md` (move), Create `docs/archive/`.
- [ ] On the PR branch: `git mv PROJECT_PLAN.md docs/archive/PROJECT_PLAN-2026-04.md && git mv HANDOFF.md docs/archive/HANDOFF-step15-2026-04.md` (if `HANDOFF.md` no longer exists on this branch, skip it).
- [ ] Add a 3-line note at the top of each archived file: "Archived 2026-09. Superseded by PLAN.md. Kept for the Stripe spec (Step 11) and Step 15 history."
- [ ] Copy this `PLAN.md` to the repo root on that branch. Commit: `docs: PLAN.md is the single plan; archive PROJECT_PLAN/HANDOFF`.

### Task 0.4 — Linear ticket + `.linear` pointer (the owner)
**Files:** Create `.linear`.
- [ ] the owner authorizes the Linear connector (claude.ai connector settings). Until then this task is blocked, not skipped.
- [ ] Create one Linear ticket "SlideMakr: paid product launch" in the product team, paste the Goal paragraph above, link PLAN.md, status `Ready for Execution`, labels `coding`, `feature`.
- [ ] Write `.linear` per `../WORKFLOW.md` format with `current_ticket`. Commit `chore: add .linear pointer`.

**GATE 0:** PR open, tests green, venv works, Linear ticket exists.

---

## Phase 1 — Finish the eval and calibrate it with the owner

**What exists (verified on `feat/read-after-write`):** creation eval (`app/eval.py`, 7 prompts, weighted composite), edit eval (`app/edit_eval.py`, 3 cases, accuracy × speed), one runner `evals/run.py` (`--creation`, `--edit`, `--both`, `--edit-variants`), calibration renderer `scripts/calibrate_edit_eval.py`. **What is missing:** N-run averaging, a human-readable calibration report, and the owner's sign-off on whether the numbers match her eye.

### Task 1.1 — Add `--repeat N` to the runner
**Files:** Modify `evals/run.py` (argparse at :76-80, `_main` :74), Test `tests/test_eval_runner.py` (new).
**Produces:** `aggregate_runs(runs: list[dict], score_key: str, id_key: str) -> dict` returning `{"n": N, "avg_overall": float, "per_case": {case_id: {"mean": float, "min": float, "max": float, "stdev": float}}}`.
- [ ] Write the failing test:
```python
# tests/test_eval_runner.py
from evals.run import aggregate_runs

def test_aggregate_runs_means_and_spread():
    runs = [
        {"avg_overall": 0.6, "results": [{"id": "retitle", "overall_score": 0.5}, {"id": "recolor", "overall_score": 0.7}]},
        {"avg_overall": 0.8, "results": [{"id": "retitle", "overall_score": 0.9}, {"id": "recolor", "overall_score": 0.7}]},
    ]
    agg = aggregate_runs(runs, score_key="overall_score", id_key="id")
    assert agg["n"] == 2
    assert abs(agg["avg_overall"] - 0.7) < 1e-9
    assert abs(agg["per_case"]["retitle"]["mean"] - 0.7) < 1e-9
    assert agg["per_case"]["retitle"]["min"] == 0.5 and agg["per_case"]["retitle"]["max"] == 0.9
    assert agg["per_case"]["recolor"]["stdev"] == 0.0
```
- [ ] Run `slidemakr-venv/bin/python -m pytest tests/test_eval_runner.py -v` → FAIL (ImportError).
- [ ] Implement in `evals/run.py`:
```python
import statistics

def aggregate_runs(runs, score_key, id_key):
    per = {}
    for r in runs:
        for case in r.get("results", []):
            per.setdefault(case[id_key], []).append(float(case.get(score_key) or 0.0))
    per_case = {
        cid: {"mean": statistics.fmean(v), "min": min(v), "max": max(v),
              "stdev": statistics.pstdev(v) if len(v) > 1 else 0.0}
        for cid, v in per.items()
    }
    return {"n": len(runs), "avg_overall": statistics.fmean(r["avg_overall"] for r in runs), "per_case": per_case}
```
  Check the real key names first: `grep -n "avg_overall\|overall_score\|\"id\"" app/eval.py app/edit_eval.py` and use whatever the run dicts actually emit (the scout saw `avg_overall` at the top level of `results/edit/*.json`).
- [ ] Add `--repeat N` (default 1) to argparse. In `_main`, loop N times per selected kind, write each run's JSON as today, then write `results/<kind>/<timestamp>_aggregate_n<N>.json` and print a table: case | mean | min | max | stdev, plus the deck links from every run.
- [ ] Tests pass. Commit `feat(evals): --repeat N with per-case mean/spread`.

### Task 1.2 — Calibration report generator
**Files:** Modify `scripts/calibrate_edit_eval.py`, Create `scripts/calibration_report.py`.
**Produces:** `results/calibration/report.html` — one card per scored slide: PNG, instruction, transcript, every sub-score, deck link. Cards in one CSS grid, stack under 900px.
- [ ] `scripts/calibration_report.py` reads the newest `results/creation/*_aggregate*.json`, newest `results/edit/*_aggregate*.json`, and the PNGs in `results/calibration/`, and writes `report.html` (inline CSS, no CDN). Include a top table: eval | n | mean | min | max.
- [ ] Extend `scripts/calibrate_edit_eval.py` to also render the first slide of each creation deck (`app/slidemakr.py` already has the thumbnail/render helper used by `_render_touched_slides` in `app/edit_runner.py:112`; reuse it).
- [ ] Commit `feat(evals): HTML calibration report`.

### Task 1.2b — Run the adherence prompts too (gap found 2026-09-16)
**Files:** Modify `app/eval.py` (`run_full_eval` loop at ~:463 and the summary loop at ~:539), Test `tests/test_eval_adherence.py`.
`FIRST_SHOT_ADHERENCE_PROMPTS` (`flowchart_on_slide_6`, `specific_chart_and_bullets`) are defined and documented in `evals/README.md` but `run_full_eval` iterates only `EVAL_PROMPTS`, so the baseline scores 5 prompts, not 7.
- [ ] Failing test: with a fake `generate_fn`, `run_full_eval` returns 7 results whose `prompt_id`s include both adherence ids, and their `scores` carry `instruction_adherence`.
- [ ] Implement: iterate `EVAL_PROMPTS + FIRST_SHOT_ADHERENCE_PROMPTS` in both loops; adherence prompts are scored via the existing contract path (`score_contract_adherence_from_state`). Keep per-prompt SLA seconds from each prompt dict.
- [ ] Tests green. Commit `fix(evals): run the first-shot adherence prompts in the creation eval`.

### Task 1.3 — DONE 2026-09-16 (one-time, 52-deck baseline; decks deleted afterwards)
Creation n=3 over 7 prompts: 0.845 (plain decks ~0.99; "X on slide N" prompts 0.64–0.69; branded 0.82; flowchart 0.80). Edit n=3: 0.63 (retitle 0.72, flowchart 0.70, recolor 0.47 with one STT-noise zero). Vision usability ~0.4 caps every edit at ~0.7. the owner's verdict: too many decks, no cost control, editing is what breaks. Replaced by Task 1.4.

### Task 1.4 — One-deck edit-flow eval with a hard budget (the owner's design, 2026-09-16)
**Files:** Create `app/edit_flow_eval.py`, `tests/test_edit_flow_eval.py`; Modify `evals/run.py`, `evals/README.md`.
**Rule:** one eval run = ONE deck created, then a fixed script of edits applied to THAT deck, each edit scored. Creation eval stays available only behind `--creation`. Nothing runs without a printed cost plan that fits the budget.
- [ ] `EDIT_FLOW = {"create": <one 4-slide prompt about remote work: title, benefits bullets, challenges, takeaways>, "edits": [...]}` with these ordered text edits (no TTS/STT, no vision): 1 `change the title of slide 1 to Remote Work Playbook`; 2 `recolor the bullets on slide 2 to purple` (plain colour word, no brand lookup); 3 `add a bar chart of office vs remote productivity to slide 3`; 4 `add a vertical flowchart to slide 4 showing plan then build then ship`; 5 `change the subtitle of slide 1 to A field guide for distributed teams`; 6 `delete the chart on slide 3`.
- [ ] `async run_edit_flow(create_fn, edit_fn, *, deck_id: str | None = None) -> dict`: creates the deck via `create_fn(prompt)` (or reuses `deck_id` with no creation), then for each edit calls `edit_fn(deck_id, instruction)` (= `app.edit_runner.run_text_edit`), re-reads state, scores with `app.edit_assess.assess` (`landed`, `usability`) and `edit["duration_seconds"]`. Per-edit overall = `landed × usability × speed` where speed = `min(1, 20 / seconds)`. Edit 6 (delete) is scored by "no image element left on slide 3" since `assess` has no delete kind — add a `delete` kind to `build_edit_contract`/`_score_requirement` in `app/edit_assess.py` for `delete|remove the <chart|image|flowchart> on slide N`. Run dict: `{"deck_id", "deck_url", "created": bool, "create_seconds", "edits": [{"i","instruction","landed","usability","seconds","overall","missing"}], "avg_overall", "total_seconds"}`.
- [ ] `evals/run.py`: new flag `--edit-flow` and it is the DEFAULT when no kind flag is given (`--both` keeps meaning creation+edit, explicit). `--deck <id>` reuses a deck. `--budget-decks N` (default 1): before running anything, compute planned decks (edit-flow: 1, or 0 with `--deck`; creation: prompts×repeat; edit: (1+3)×repeat) and planned LLM turns (edit-flow: 1 + len(edits)); print `PLAN: decks=<n> llm_turns=<n> budget=<N>`; if decks > budget exit 2 with a clear message and run nothing. Results to `results/edit_flow/<timestamp>.json`; print the deck link once at the top, then a table `# | edit | landed | usability | secs | overall`, then `avg_overall`. `--repeat` applies (aggregate as today, keys `avg_overall`/`i`/`overall`).
- [ ] Tests (no network; fake `create_fn`, fake `edit_fn` that mutates a dict state, monkeypatched `slidemakr.get_presentation_state`): all 6 edits scored; a missed edit yields landed 0 and `missing`; `--deck` skips creation; budget refusal exits 2 before any call; default kind is edit-flow; delete-kind contract parses and scores.
- [ ] **Reasons + honest names (the owner, 2026-09-16):** every sub-score carries a plain-English `reasons` sentence (what was measured, on which slide, which objects) printed under each table row and kept in JSON; `overlap_details()` in `app/layout_quality.py` names overlapping pairs; an image overlapping a text element by >10% caps that slide's overlap score at 0.3 (no deck-wide dilution); `error_rate` renamed `api_success` everywhere (1.0 = every API request succeeded).
- [ ] README: replace the "Run it" block with the new default and the budget rule. Commit `feat(evals): one-deck edit-flow eval with hard deck budget`.
- [ ] Then run it ONCE: `python -m evals.run` (1 deck). Show the owner the table and the deck link. That is GATE 1.

### Task 1.5 — Merge the trunk and deploy it (after GATE 1)
- [ ] Merge the PR from 0.2 into `main` (merge commit, not squash, to keep the stack history).
- [ ] `./deploy.sh`. Verify: the live URL creates a deck by text, and `/edit-text` edits it (STT→text path, no native-audio websocket needed).
- [ ] Update Linear: phase 1 done, baseline numbers, revision id.

---

## Phase 2 — Stripe credits in production

**What exists:** commit `30e09b5` on `codex/slidemakr-stripe` implements the whole Step 11 spec (`app/stripe_billing.py`, credit ledger in `app/db.py`, `/billing/checkout`, `/billing/webhook` with signature check, `/billing/credits`, 402 gating on `/generate` and `/generate-audio`, deploy.sh secrets, 2 test files). **Gaps found by review:** identity is a client-supplied guest id (infinite free decks), credits are burned before generation and never refunded, webhook credits are lost if the user doc does not exist, no balance display or package picker in the UI, `/edit-text` and `/edit-audio` are not gated.

### Task 2.1 — Cherry-pick the Stripe commit onto main
- [ ] `git checkout -b feat/stripe-credits main && git cherry-pick 30e09b5`. Expect conflicts only in `app/db.py` and `app/server.py` (main changed both). Resolve by keeping main's code and adding the Stripe blocks.
- [ ] Tests green including `tests/test_stripe_billing.py` and `tests/test_billing_endpoints.py`. Commit.

### Task 2.2 — Identity: free deck for guests, purchases and balances only for signed-in users
**Files:** Modify `app/server.py` (`_billing_user_id`), `app/static/index.html`, Test `tests/test_billing_endpoints.py`.
**Rule:** if the request carries a logged-in Google session (`app/auth.py` session user), `user_id` = that email. Otherwise `user_id` = `guest:<localStorage id>` and the only thing a guest can do is the one free deck. `POST /billing/checkout` returns 401 `{login_required: true}` for guests. The 402 response for a guest is `{checkout_required: true, login_required: true}`.
- [ ] Failing test: guest POSTs `/billing/checkout` → 401 with `login_required`. Signed-in (mock session) → 200 with `url`.
- [ ] Failing test: guest's second `/generate` → 402 with both flags.
- [ ] Implement in `_billing_user_id` (server.py ~:165) and the checkout route. Frontend: on `login_required`, show the existing "Sign in with Google" prompt instead of the package picker.
- [ ] Residual risk to state in the report: a guest can clear localStorage for another free deck. Accepted for launch; revisit with IP rate limiting if abused.

### Task 2.3 — Refund on failure, robust webhook credit
**Files:** Modify `app/db.py` (`add_user_credits`, `consume_generation_credit`), `app/server.py` (`/generate`, `/generate-audio`), Test `tests/test_stripe_billing.py`.
- [ ] Failing test: `consume_generation_credit` then simulated generation exception → `refund_generation_credit(user_id)` restores the balance.
- [ ] Failing test: `add_user_credits` for a never-seen user id creates the doc (use `set(..., merge=True)` with `Increment`, not `update`).
- [ ] Implement; wrap `_run_generation` calls so any exception refunds before re-raising.
- [ ] Gate `/edit-text` and `/edit-audio` the same way as `/generate` (one credit per edit turn, first deck free applies to creation only). Test it.

### Task 2.4 — Billing UI
**Files:** Modify `app/static/index.html`.
- [ ] Header badge "Credits: N" from `GET /billing/credits` when signed in.
- [ ] Package picker card (3 cards in one CSS grid, equal size, stack under 900px): 10 / $4.99, 50 / $19.99, 100 / $29.99. Each button POSTs `/billing/checkout` and redirects to `url`. Remove the native `confirm()`.
- [ ] Handle the return URL `?checkout=success` and `?checkout=cancel`: refresh the badge, show a one-line toast.
- [ ] Browser verification: screenshots of picker, badge, 402 flow, in the report.

### Task 2.5 — Stripe account and secrets (the owner, cannot be done by an agent)
- [ ] Create the Stripe account, turn on dynamic payment methods, copy the **test** secret key and create a test webhook endpoint for `https://<cloud-run-url>/billing/webhook` (event `checkout.session.completed`).
- [ ] Put `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` in GCP Secret Manager (`gcloud secrets create ...`) and in `app/.env` locally. Agents never type these.
- [ ] Add the two `gcloud secrets create` commands to `docs/deploy.md` (new) so the step is repeatable.

### Task 2.6 — End-to-end test matrix (test mode)
- [ ] Local: `stripe listen --forward-to localhost:8080/billing/webhook`. Run the matrix and paste results: free first deck | login required to buy | buy 10 with `4242 4242 4242 4242` | webhook credits +10 | `/generate` deducts 1 | declined `4000 0000 0000 0002` leaves balance unchanged | refund on forced generation failure.
- [ ] `python -m evals.run --creation --repeat 1` still ≥ the Phase 1 baseline minus 0.02 (the gate must not slow creation).

### Task 2.7 — GATE 2 and go live
- [ ] the owner reviews the matrix and screenshots. Merge `feat/stripe-credits` → `main`.
- [ ] the owner completes the Stripe go-live checklist (https://docs.stripe.com/get-started/checklist/go-live) and swaps the two secrets to live values. `./deploy.sh`.
- [ ] the owner makes one real $4.99 purchase on the live URL and gets 10 credits. That is the definition of "paid product exists". Log it in Linear.

---

## Phase 3 — Trust docs and the sales block (revenue before more features)

### Task 3.1 — Security & privacy page, factual voice
**Files:** cherry-pick `0973f9b` (`docs/security-privacy.md`, `LAUNCH.md`, `tests/test_launch_docs.py`), then edit.
- [ ] Rewrite every "should" into what the code does today, with a file reference per claim. Claims with no code behind them (data deletion) become a short "Not yet: request deletion by email" line. Serve it at `GET /privacy` (static route) and link it from the footer.
- [ ] Tests green. Merge as its own PR.

### Task 3.2 — Fill the sales block
**Files:** `docs/selling/sell-block-1.md` (untracked in the main checkout; commit it only if it contains no names or personal data, else keep it out of the repo).
- [ ] Replace every `[link]` with the live URL and the privacy page. Fill Batch A target names (the owner). Start the 2-week block on 2026-09-21 as written. Sends are logged in the file's table; the Linear ticket gets a weekly count.

---

## Phase 4 — Google Slides add-on (the distribution wedge)

**Exists:** commit `94b72a4` — `slides_addon/{Code.gs,Sidebar.html,appsscript.json}`, `POST /api/addon-token`, `_resolve_addon_user` (server.py ~:521-546), shape tests. **Gaps:** tokens live in a process dict (lost on Cloud Run restart, unbounded), `Code.gs` hardcodes `https://slidemakr.com`, no deployment steps.

- [ ] **Domain decision (the owner):** does slidemakr.com exist and point at Cloud Run? If not, use the Cloud Run URL everywhere via one constant in `Code.gs` and `Sidebar.html`.
- [ ] Cherry-pick `94b72a4` onto a branch from main; resolve `server.py`.
- [ ] Move addon tokens to Firestore collection `addon_tokens` with `expires_at`; `_resolve_addon_user` rejects expired tokens; add `db.purge_expired_addon_tokens()` called on token issue. Tests for issue, resolve, expiry.
- [ ] Wire the sidebar to `POST /edit-audio` (STT→text path) with the active presentation id; the credit gate from Phase 2 applies.
- [ ] `docs/slides-addon.md`: clasp push, test deployment, adding a test user, private Marketplace listing steps.
- [ ] GATE 4: the owner installs the test deployment in her own Slides and makes one voice edit. Screenshot in Linear.

---

## Phase 5 — Feedback editing, PowerPoint add-in, GTM assets

### Task 5.1 — Feedback editing (`834a7f3`)
- [ ] Cherry-pick. Fix the auth hole: `POST /api/comments/apply-feedback` requires either a valid addon token or a logged-in session; remove the `"feedback_runner"` fallback. Test: anonymous → 401.
- [ ] Add real Drive comment ingestion: `slidemakr.list_comments(presentation_id)` via Drive API `comments.list`, feed into `build_feedback_edit_prompt`, resolve applied comments via `comments.update(resolved=True)`. Tests with a mocked Drive client.

### Task 5.2 — PowerPoint add-in (`f25156b`)
- [ ] Cherry-pick. Replace the manifest `<Id>` with a real GUID (`python -c "import uuid;print(uuid.uuid4())"`). Serve `powerpoint_addon/src/taskpane.html` at `/powerpoint/taskpane.html` from FastAPI static mount; make sure `/static/images/webclip.png` exists.
- [ ] Sideload test in PowerPoint for the web (the owner). Document in `docs/powerpoint-addin.md`. AppSource submission is out of scope for this plan.

### Task 5.3 — GTM assets (`a2e0016`)
- [ ] Cherry-pick the three docs. Turn `docs/blog-ai-slide-creation.md` from an outline into a 600-word draft using the real baseline numbers from Phase 1. `docs/video-demo-plan.md`: drop scenarios for features not live. Keep `tests/test_gtm_assets.py` green.

---

## Not in scope

- Step 9 "time saved" wait UX, Step 10 multi-agent speed, Step 14 session history.
- Native-audio `/ws` voice path improvements (kept only as A/B fallback).
- AppSource / public Marketplace submissions.
- Any change to how OAuth test users are managed.

## Acceptance (the whole plan is done when)

- [ ] `main` == the trunk stack + Stripe + trust docs + add-on + feedback + PowerPoint + GTM, each landed as its own reviewed merge.
- [ ] `python -m evals.run --both --repeat 3` produces numbers the owner signed off on, recorded in `evals/README.md`, and each phase's run is ≥ baseline − 0.02.
- [ ] One real purchase completed on the live URL; credits deducted per generation; refund on failure verified.
- [ ] The Linear ticket holds every decision, gate verdict, and revision id. `PROJECT_PLAN.md` and `HANDOFF.md` live in `docs/archive/`.
