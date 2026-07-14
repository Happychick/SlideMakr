# SlideMakr — agent constitution

AI agent that creates and edits Google Slides from voice and text (Google ADK +
Gemini 2.5 Flash + Slides/Drive APIs).

## The Goal (north star)

**Make the slides the user asked for, in the least time.** Fast *and* accurate —
neither alone counts. Every instruction below serves this.

**How we know it did well — the eval metric.** Success is measured, not guessed:
`python -m evals.run --both` scores each run on **accuracy × speed** (accuracy =
did-it × usability; speed = time incl. STT) and writes to `results/`. A change is
only good if the score holds or rises. See [evals/README.md](evals/README.md).

## Map (where knowledge lives)

- **How to build/edit slides** → [app/skills/google_slides.md](app/skills/google_slides.md) — the single source of slide know-how (ships with the app; the agent loads it at runtime).
- **Every tool the agent has** → [docs/TOOLS.md](docs/TOOLS.md).
- **How to evaluate** → [evals/README.md](evals/README.md); results in `results/`.
- **Roadmap / backlog** → [PROJECT_PLAN.md](PROJECT_PLAN.md) (active) ← [FUTURE.md](FUTURE.md) (idea inbox).
- **Product overview / setup** → [README.md](README.md).
- **Official request reference** (don't store it — look it up): Slides REST API
  <https://developers.google.com/slides/api/reference/rest> and batchUpdate
  requests <https://developers.google.com/slides/api/reference/rest/v1/presentations/request>.

## Commands

```bash
source slidemakr-venv/bin/activate                 # activate venv (always)
uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload   # dev server
pip install -r requirements.txt                    # deps
python -m pytest tests/ -q                         # tests (must stay green)
python -m evals.run --both                         # eval (writes results/)
export PATH=$HOME/google-cloud-sdk/bin:$PATH && ./deploy.sh   # deploy to Cloud Run
```

## Key patterns

- **Narrow tools + commit**: editing uses ~29 narrow typed tools (one per Slides
  API request) buffered by `app/slide_batch.py`, flushed by `commit_edits` in one
  `batchUpdate`. Hallucinated request types are structurally impossible. The old
  monolithic `execute_slide_requests` lives in `slidemakr.py` (called internally).
- **Validation** (`slides_schema.py`): `validate_requests()` auto-fixes common
  Gemini mistakes (hex→RGB, bad fields) and drops invalid request types.
- **Object-ID safety**: targeted edits validate the objectId against the live deck
  and reject invented/stale IDs (returns the real valid IDs).
- **EMU coordinates**: 1 inch = 914,400 EMU; slide = 9,144,000 × 5,143,500 EMU.

## Gotchas

- Models: `text_agent`/creation = `gemini-2.5-flash`; `edit_agent` (voice) =
  `gemini-2.5-flash-native-audio-latest` (unreliable at reading tool responses —
  being replaced by an STT→text pipeline).
- Creation agent must NOT call `review_slide_layout` (it overrides template layouts);
  the reviewer is for editing + evals only.
- Firestore has an in-memory fallback — local dev works without GCP creds.
- `*.json` is gitignored (service-account keys). Service-account key is rotated via
  Secret Manager `SERVICE_ACCOUNT_JSON` (see PROJECT_PLAN / memory for the current key).
- OAuth is in Testing mode — test users added manually in GCP console.

## Environment (`app/.env`)

`GOOGLE_API_KEY` (Gemini) · `SERVICE_ACCOUNT_PATH` (local) / `SERVICE_ACCOUNT_JSON`
(prod) · `GOOGLE_CLOUD_PROJECT=slidemakr` · `GOOGLE_OAUTH_CLIENT_ID/SECRET` ·
`UNSPLASH_ACCESS_KEY`. Full list + descriptions in [README.md](README.md).
