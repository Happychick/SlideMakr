# Session handoff — 2026-07-14

Context for resuming SlideMakr work in a fresh window. Pairs with the memory files
under `~/.claude/projects/-Users-christinastejskalova-SlideMakr/memory/`
(MEMORY.md indexes them).

## The north star (how we work now)

**Goal-driven, not roadmap-driven:** *make the slides the user asked for, in the
least time* (fast **and** accurate). Every change is validated by the eval metric
("autoverse" — deploy the strategy that scores best), never guessed. See
`CLAUDE.md` (66-line constitution) and `evals/README.md`.

## Branch stack (all pushed to origin; nothing merged to main yet)

```
main  (7fa58e5: Step 15 narrow tools + object-ID validation — DEPLOYED to prod, rev slidemakr-00014-l8r)
 └─ integrate/adherence-eval   the fast+accurate eval harness (creation + edit)
     └─ chore/repo-cleanup       clean agent setup — PR #1 OPEN (base = integrate/adherence-eval)
         └─ feat/voice-swap      STT→text edit pipeline + UI (latest tip: dc9aa49)
```
Merge bottom-up when ready (PR #1 first). Each layer builds on the one above.

## What's done this session

- **Prod restored:** service-account key was revoked (locally + prod). Rotated
  Secret Manager `SERVICE_ACCOUNT_JSON` to valid key `155715f1…`; local key file at
  `studio/slidemakr-639063d4537a.json`. `main` deployed. Live create verified.
- **Eval metric (the yardstick):** `python -m evals.run --both` → writes
  `results/<creation|edit>/*.json`, prints per-case scores **+ deck links**,
  auto-shares decks if `EVAL_SHARE_EMAIL` set (it is: christina.stejskalova@gmail.com).
  Creation composite (adherence 35% + speed 20% + …) ~**0.90**. Edit composite =
  **accuracy × speed**, accuracy = instruction-landed × usability(deterministic
  font/balance/title/fit + vision review). Edit avg ~**0.58–0.67** (NOISY — agent
  variance; compare multi-run averages, not single runs).
- **Clean repo:** lean `CLAUDE.md`+`AGENTS.md`; single slide-knowledge source
  `app/skills/google_slides.md` (agents load it at runtime; references the live
  Slides API docs, doesn't store request shapes); `docs/TOOLS.md`; one eval runner;
  removed dead code. (PR #1.)
- **Voice swap (Step b):** `/edit-audio` + `/edit-text` endpoints (audio → Gemini
  STT → `gemini-2.5-flash` + narrow tools via `app/edit_runner.run_text_edit`,
  silent mode). Edit UI rewired to record→POST→refresh iframe. Native-audio `/ws`
  kept as A/B fallback. **Reliable — no 1011 crashes** (the native-audio failure
  mode). Verified: `/edit-text` end-to-end in browser; `/edit-audio` server-side.
- **Flowchart quality:** always titled now (`create_flowchart` defaults title).

## Verified facts / gotchas

- 119 tests pass (`python -m pytest tests/ -q`). Always keep green.
- gcloud at `~/google-cloud-sdk/bin/gcloud` (not on PATH). Deploy:
  `export PATH=$HOME/google-cloud-sdk/bin:$PATH && ./deploy.sh` (ships **only `app/`**).
- `.claude/`, `.planning/`, `FUTURE.md`, `results/`, `*.json` are gitignored.
- Editing "slide 1": the agent sometimes interprets it 0-indexed — an agent-quality
  ambiguity the eval catches, not a bug.

## Next up (priority order)

1. **Live-mic test** of `/edit-audio` in the browser (only the user can do this).
2. **Step c — read-after-write edits** (task #11): after `commit_edits`, re-read
   state so the edit agent verifies/self-corrects. Now meaningful since the edit
   agent is a text model that reads tool responses. Validate via edit eval.
3. **Multi-run edit eval** (3–5×, average) to get a trustworthy edit baseline.
4. **Relocate PROJECT_PLAN.md** into docs/ or a planning/ home (task #17, deferred).
5. **Integrate remaining codex branches** onto main in roadmap order (Stripe next) —
   see memory `codex-branch-stack`.
6. Agent-quality wins the metric flags: branding actually applying colors
   (branded_deck adherence ~0.6), recolor robustness under noise.

## Key files
- `CLAUDE.md` / `AGENTS.md` — constitution. `app/skills/google_slides.md` — slide know-how.
- `evals/run.py` + `evals/README.md` + `results/` — the metric.
- `app/edit_runner.py` — the one text→edit engine (eval + product). `app/eval.py`,
  `app/edit_eval.py`, `app/layout_quality.py`, `app/instruction_contract.py` — scoring.
- `app/server.py` — `/edit-audio`, `/edit-text`, `/generate`, `/ws`, `/admin/run-eval`.
