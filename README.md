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

## For judges / testers: Google OAuth access

SlideMakr uses Google OAuth for the "Sign in with Google" feature (to save presentations to your Drive and enable voice editing). Since the app is in **Testing** mode on Google Cloud, only pre-approved test users can sign in.

**To test the full flow, you need to be added as a test user:**

1. Go to [Google Cloud Console > APIs & Services > OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)
2. Select the **slidemakr** project
3. Under **Test users**, click **Add users**
4. Enter the Gmail address of the tester
5. Save -- the tester can now sign in immediately

**What works without login:**
- Creating presentations via voice or text (full feature)
- Viewing the generated slides in the embedded preview
- Voice editing (real-time conversational editing)

**What requires login:**
- Saving presentations to your Google Drive

## Test plan

Use **Chrome** for best speech recognition support. Allow microphone access when prompted.

### 1. Voice creation (no login required)
- [ ] Open the app URL
- [ ] Click the microphone button and say: *"Create a 4-slide pitch deck about AI in healthcare. Include a title slide, a slide about current challenges with bullet points, a slide with a flowchart showing how AI diagnosis works, and a closing slide with key takeaways."*
- [ ] Verify: slides appear in the embedded preview within ~30 seconds
- [ ] Verify: title slide has a title and subtitle
- [ ] Verify: bullet points are readable and properly formatted
- [ ] Verify: flowchart slide has connected nodes with labels
- [ ] Scroll through all slides in the preview

### 2. Voice editing (no login required)
- [ ] Click **"Edit with Voice"** on the created presentation
- [ ] Say: *"Make the title slide background dark blue and change the title font to white."*
- [ ] Verify: changes appear in the preview, agent confirms vocally
- [ ] Say: *"On slide two, add a bar chart showing AI adoption rates: 2020 at 20%, 2022 at 35%, 2024 at 55%."*
- [ ] Verify: chart appears on the slide
- [ ] Say: *"Add an image related to healthcare technology on the first slide."*
- [ ] Verify: image appears on the slide
- [ ] Say: *"Add a text box on slide 3 that says 'Source: WHO 2024 Report'."*
- [ ] Verify: text box appears and is readable

### 3. Text creation (alternative input)
- [ ] Click **"Create another presentation"** to return to the home screen
- [ ] Type in the text field: *"Create a 3-slide presentation about renewable energy with a pie chart"*
- [ ] Verify: slides are created with a pie chart on one of the slides

### 4. Google OAuth + Drive sharing (login required)
- [ ] Click **"Sign in with Google"** (must be a registered test user)
- [ ] Complete the Google OAuth flow
- [ ] Verify: presentation is automatically shared to your Google Drive
- [ ] Click **"Open in Google Slides"** and verify the deck opens in Google Slides

### 5. Brand theming
- [ ] Create a new presentation: *"Create a 3-slide pitch deck for Google. Use their brand colors and fonts."*
- [ ] Verify: slides use Google's brand colors (blue, red, yellow, green)

### 6. Email sharing (no login required)
- [ ] After creating a presentation (without logging in), enter an email address in the share field
- [ ] Click **Share**
- [ ] Verify: the presentation link is shared to that email

## License

MIT
