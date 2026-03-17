# SlideMakr

AI agent that creates and edits Google Slides presentations from voice and text,
using Google ADK + Gemini 2.5 Flash and the Google Slides/Drive APIs.

## Commands

```bash
# Start dev server
source slidemakr-venv/bin/activate
uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload

# Install dependencies
pip install -r requirements.txt
```

## Architecture

```
SlideMakr/
  app/
    server.py           # FastAPI server: /generate, /ws, /share, auth routes
    agent.py            # ADK agents: text_agent (creation), edit_agent (voice editing)
    slidemakr.py        # Google Slides/Drive API operations
    auth.py             # Google OAuth SSO (login/callback/logout/me)
    db.py               # Firestore data layer (with in-memory fallback)
    static/
      index.html        # Full frontend (Webflow + inline JS)
      audio-processor.js # AudioWorklet for voice editing PCM capture
  slidemakr-venv/       # Primary virtualenv
```

## Flows

### Voice/Text Creation
1. User speaks (SpeechRecognition API) or types instructions
2. Text → POST /generate → text_agent (Gemini 2.5 Flash)
3. Agent calls: create_new_presentation → execute_slide_requests
4. Returns presentation URL + preview iframe

### Voice Editing (bidi streaming)
1. User clicks "Edit with Voice" → WebSocket /ws?presentation_id=X
2. edit_agent receives audio via ADK LiveRequestQueue
3. Agent reads presentation state, executes edit commands
4. Changes reflected in embedded iframe

### Auth Flow
1. Slides created without login
2. After creation: "Sign in with Google" prompt
3. OAuth → auto-share presentation to user's Drive
4. User can then edit with voice and access existing presentations

## Environment (app/.env)

```
GOOGLE_API_KEY=...              # Gemini API key
SERVICE_ACCOUNT_PATH=...        # Google service account JSON (for Slides/Drive API)
GOOGLE_CLOUD_PROJECT=slidemakr  # Firestore project
GOOGLE_OAUTH_CLIENT_ID=...      # OAuth (optional for local dev)
GOOGLE_OAUTH_CLIENT_SECRET=...  # OAuth (optional for local dev)
```

## Key Details

- text_agent uses `gemini-2.5-flash` for reliable tool calls via /generate
- edit_agent uses `gemini-2.5-flash-native-audio-latest` for bidi voice editing
- execute_slide_requests separates structural (createSlide, createShape) from content
  requests — structural batched together, content one-by-one for error isolation
- Firestore has in-memory fallback so local dev works without GCP credentials
- AudioWorklet processor registers as 'pcm-capture' (audio-processor.js)
