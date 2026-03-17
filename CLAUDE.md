# SlideMakr

AI agent that creates and edits Google Slides presentations from voice and text,
using Google ADK + Gemini 2.5 Flash and the Google Slides/Drive APIs.

**Live:** https://slidemakr-72711045873.us-central1.run.app
**Repo:** https://github.com/Happychick/SlideMakr

## Commands

```bash
# Start dev server
source slidemakr-venv/bin/activate
uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload

# Install dependencies
pip install -r requirements.txt

# Deploy to Cloud Run
./deploy.sh

# Docker local build
docker build -t slidemakr . && docker run -p 8080:8080 slidemakr
```

## Architecture

```
SlideMakr/
  app/
    server.py           # FastAPI: /generate, /ws, /share, auth routes
    agent.py            # ADK agents: text_agent (creation) + edit_agent (voice editing)
    slidemakr.py        # Google Slides/Drive API: create, read, batchUpdate, share
    slides_schema.py    # Request validation + auto-fix (colors, types, bounds)
    flowchart.py        # BFS-based flowchart layout engine (vertical/horizontal/auto)
    auth.py             # Google OAuth SSO (login/callback/logout/me)
    db.py               # Firestore data layer (in-memory fallback for local dev)
    static/
      index.html        # Full frontend (Webflow export + inline JS)
      audio-processor.js # AudioWorklet for voice editing PCM capture
  deploy.sh             # Cloud Run deployment (Cloud Build + Secret Manager)
  Dockerfile            # Python 3.11-slim, port 8080
  requirements.txt      # google-adk, fastapi, google-api-python-client, authlib
```

## Flows

### Voice/Text Creation
1. User speaks (SpeechRecognition API) or types instructions
2. Text → POST /generate → text_agent (Gemini 2.5 Flash)
3. Agent calls: create_new_presentation → execute_slide_requests
4. Returns presentation URL + embedded preview iframe

### Voice Editing (bidi streaming)
1. User clicks "Edit with Voice" → WebSocket /ws?presentation_id=X
2. edit_agent receives audio via ADK LiveRequestQueue
3. Agent reads presentation state, executes edit commands
4. Changes reflected in embedded iframe (works WITHOUT login)

### Auth Flow
1. Slides created without login
2. After creation: "Sign in with Google" prompt
3. OAuth → auto-share presentation to user's Drive

## Key Patterns

### Smart Batch Execution
`slidemakr.py:execute_slide_requests` separates requests into two phases:
- **Structural** (createSlide, createShape) → batched together (fast)
- **Content** (insertText, updateTextStyle) → one-by-one (error isolation)
If a content request fails, the rest still succeed. Errors logged to Firestore via `db.record_error()`.

### Request Validation (slides_schema.py)
`validate_requests()` auto-fixes common Gemini mistakes before hitting the API:
- Color format normalization (hex → RGB float)
- Invalid request type detection + drop
- Field validation per request type

### Flowchart Engine (flowchart.py)
BFS-based layout with three modes: vertical, horizontal, auto-detect.
Uses EMU positioning with overflow detection for slide bounds.

### EMU Coordinate System
Google Slides uses EMU (English Metric Units): **1 inch = 914,400 EMU**.
Slide dimensions: 9,144,000 × 5,143,500 EMU (10" × 5.625").

## Environment (app/.env)

```
GOOGLE_API_KEY=...              # Gemini API key
SERVICE_ACCOUNT_PATH=...        # Google service account JSON (local dev)
SERVICE_ACCOUNT_JSON=...        # Service account JSON string (production)
GOOGLE_CLOUD_PROJECT=slidemakr  # Firestore project
GOOGLE_OAUTH_CLIENT_ID=...      # OAuth (optional for local dev)
GOOGLE_OAUTH_CLIENT_SECRET=...  # OAuth (optional for local dev)
UNSPLASH_ACCESS_KEY=...         # Image search
```

## Deployment

- **Platform:** Google Cloud Run (Docker container)
- **Build:** `./deploy.sh` runs Cloud Build → pushes container → deploys revision
- **Secrets:** GCP Secret Manager (GOOGLE_API_KEY, SERVICE_ACCOUNT_JSON, OAuth creds)
- **Port:** 8080 in production, 8000 in local dev

## Gotchas

- text_agent uses `gemini-2.5-flash`; edit_agent uses `gemini-2.5-flash-native-audio-latest`
- Do NOT call `review_slide_layout` during creation — it overrides template layouts
- AudioWorklet registers as 'pcm-capture' (audio-processor.js)
- Firestore has in-memory fallback — local dev works without GCP credentials
- `*.json` is in .gitignore to prevent service account key leaks
- OAuth is in Testing mode — test users must be manually added in GCP console
