# SlideMakr

**AI agent that creates and edits Google Slides presentations from voice and text.**

Built with Google ADK + Gemini 2.5 Flash for the [Gemini Live Agent Challenge](https://googleai.devpost.com/).

**Live demo:** [slidemakr-gj4eorwgtq-uc.a.run.app](https://slidemakr-gj4eorwgtq-uc.a.run.app)

---

## What it does

SlideMakr turns natural language into professional Google Slides presentations. Speak or type what you want, and the AI agent creates complete slide decks with titles, content, images, charts, flowcharts, and custom styling.

After creation, you can **edit your slides by voice** in real-time using bidirectional audio streaming.

### Key features

- **Voice creation** -- Speak your presentation idea, get a complete slide deck
- **Voice editing** -- Edit slides conversationally ("make the title bigger", "add a chart showing Q1 revenue")
- **Flowcharts & diagrams** -- Auto-layout flowcharts with vertical, horizontal, or auto-detected layouts
- **Data charts** -- Bar, line, pie, doughnut, radar charts from natural language
- **Image search** -- AI-powered image placement from Unsplash
- **Brand theming** -- Mention a company name and get branded slides with their colors and fonts
- **Google OAuth** -- Sign in to auto-share presentations to your Drive

## Architecture

```
Browser (Voice/Text)
    |
    v
FastAPI Server (Cloud Run)
    |
    +--> text_agent (Gemini 2.5 Flash)
    |       |
    |       +--> create_new_presentation
    |       +--> execute_slide_requests (Google Slides API)
    |       +--> create_flowchart (auto-layout engine)
    |       +--> create_chart (QuickChart API)
    |       +--> search_web_image (Unsplash)
    |       +--> search_company_branding (Gemini + Google Search)
    |
    +--> edit_agent (Gemini 2.5 Flash Native Audio)
            |
            +--> get_presentation_state
            +--> execute_slide_requests
            +--> create_flowchart / create_chart
            +--> share_presentation_with_user (Drive API)
```

### How it works

1. **Creation flow:** User speaks or types instructions -> Browser SpeechRecognition converts to text -> `POST /generate` -> `text_agent` generates Google Slides API requests -> Slides created via batchUpdate

2. **Voice editing flow:** User clicks "Edit with Voice" -> WebSocket connection -> AudioWorklet captures 16kHz PCM -> ADK `LiveRequestQueue` streams to `edit_agent` -> Agent reads slide state, generates edits, responds with audio

3. **Auth flow:** Presentations created without login -> "Sign in with Google" prompt -> OAuth -> Auto-share to user's Drive -> Voice editing enabled

### Smart batch execution

`execute_slide_requests` separates structural requests (createSlide, createShape) from content requests (insertText, updateTextStyle). Structural requests run as one batch; content requests run one-by-one for error isolation. This prevents a single bad request from blocking the entire presentation.

## Tech stack

| Layer | Technology |
|-------|-----------|
| AI Framework | Google ADK (Agent Development Kit) |
| AI Model | Gemini 2.5 Flash (text), Gemini 2.5 Flash Native Audio (voice) |
| Backend | FastAPI, Python 3.11 |
| Frontend | HTML/CSS/JS, Web Speech API, AudioWorklet |
| Google APIs | Slides API, Drive API, OAuth 2.0 |
| Database | Firestore (with in-memory fallback) |
| Deployment | Cloud Run, Docker |
| Images | Unsplash API |
| Charts | QuickChart API |

## Project structure

```
app/
  server.py           # FastAPI: /generate, /ws, /share, auth routes
  agent.py            # ADK agents: text_agent + edit_agent + tools
  slidemakr.py        # Google Slides/Drive API operations
  flowchart.py        # Flowchart layout engine (vertical/horizontal/tree)
  auth.py             # Google OAuth SSO
  db.py               # Firestore data layer
  static/
    index.html        # Frontend (Webflow + inline JS)
    audio-processor.js # AudioWorklet for PCM capture
```

## Local development

```bash
# Create virtualenv
python3 -m venv venv && source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp app/.env.example app/.env
# Fill in: GOOGLE_API_KEY, SERVICE_ACCOUNT_PATH, etc.

# Run dev server
uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_API_KEY` | Gemini API key |
| `SERVICE_ACCOUNT_PATH` | Path to Google service account JSON (local dev) |
| `SERVICE_ACCOUNT_JSON` | Service account JSON string (production) |
| `GOOGLE_CLOUD_PROJECT` | GCP project ID (default: `slidemakr`) |
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth client ID (optional for local dev) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | OAuth client secret (optional for local dev) |
| `UNSPLASH_ACCESS_KEY` | Unsplash API key for image search |
| `SLIDE_TEMPLATE_ID` | Google Slides template ID (optional) |

## Deployment

Deployed on Google Cloud Run:

```bash
# Build and deploy
./deploy.sh
```

The `deploy.sh` script handles Cloud Build, container push, and Cloud Run deployment with Secret Manager integration.

## License

MIT
