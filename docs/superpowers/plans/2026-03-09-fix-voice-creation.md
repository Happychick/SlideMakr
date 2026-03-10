# Fix Voice Creation + Enable Voice Editing — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make voice-based slide creation work by replacing broken bidi streaming with browser SpeechRecognition, then wire creation → SSO → voice editing flow.

**Architecture:** Frontend uses browser-native SpeechRecognition for voice capture during creation, sends text to existing `/generate` endpoint. After slides are created, user signs in via Google OAuth (already built), presentation is auto-shared to their Drive, then they enter voice editing mode which uses bidi WebSocket streaming.

**Tech Stack:** FastAPI, Google ADK, Gemini 2.5 Flash, Google Slides/Drive API, SpeechRecognition Web API, WebSocket

**Spec:** `docs/superpowers/specs/2026-03-09-fix-voice-creation-design.md`

---

## Chunk 1: Commit & Stabilize

### Task 1: Commit existing uncommitted changes

All current work from the other chat (auth, branding, edit agent, text agent, server expansion) needs to be preserved as a commit.

**Files:**
- Staged: `app/agent.py`, `app/server.py`, `app/db.py`, `app/static/index.html`, `app/auth.py`, `app/.env.example`, `requirements.txt`, `PROJECT_PLAN.md`

- [ ] **Step 1: Stage all modified and new files**

```bash
git add app/agent.py app/server.py app/db.py app/static/index.html app/auth.py app/.env.example requirements.txt PROJECT_PLAN.md
```

- [ ] **Step 2: Commit with descriptive message**

```bash
git commit -m "feat: add auth, branding, edit agent, text agent from prior session

Preserves all work from the other chat session:
- auth.py: Google OAuth SSO (login/callback/logout/me)
- agent.py: branding tool, edit agent, text agent separation
- server.py: session middleware, text/edit runners, auto-share, timeout
- db.py: users collection, get_user_presentations
- index.html: auth bar, slide preview, editor view, voice controls
- requirements.txt: authlib, itsdangerous additions

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

- [ ] **Step 3: Verify clean working tree**

```bash
git status
```

Expected: `nothing to commit, working tree clean` (except studio/ and untracked files)

### Task 2: Verify server starts and /generate works

**Files:**
- Read: `app/server.py`, `app/auth.py`

- [ ] **Step 1: Check the server starts without import errors**

```bash
cd /Users/christinastejskalova/SlideMakr
source slidemakr-venv/bin/activate
python -c "from app.server import app; print('Server imports OK')"
```

Expected: `Server imports OK` (no import errors)

- [ ] **Step 2: Fix any import issues**

If the import fails, likely causes:
- `authlib` not installed → `pip install authlib itsdangerous`
- Missing env vars → server should start without OAuth configured (auth.py logs a warning but doesn't crash)

- [ ] **Step 3: Start the server and test /generate**

```bash
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

In a separate terminal:
```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"text": "Create a 2-slide presentation about testing"}'
```

Expected: JSON response with `success: true`, `presentation_url`, `presentation_id`

- [ ] **Step 4: Commit any fixes**

```bash
git add -A && git commit -m "fix: resolve startup issues after prior session merge"
```

---

## Chunk 2: Voice Creation with SpeechRecognition

### Task 3: Replace AudioWorklet voice capture with SpeechRecognition

The current voice flow opens a WebSocket, streams PCM audio via AudioWorklet, and relies on the bidi voice agent (which is broken). Replace it with browser-native SpeechRecognition that captures text, then sends it to `/generate`.

**Files:**
- Modify: `app/static/index.html` (JS section, lines ~656-1110)

- [ ] **Step 1: Replace `startVoice()` with SpeechRecognition-based implementation**

Replace the entire `startVoice()` function (currently at ~line 879-959) with:

```javascript
async function startVoice() {
  const micBtn = document.getElementById('micButton');
  const voiceStatus = document.getElementById('voiceStatusMessage');

  // Check for SpeechRecognition support
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    voiceStatus.textContent = 'Speech recognition not supported in this browser. Please type instead.';
    document.getElementById('field').focus();
    return;
  }

  try {
    // Request mic permission first (SpeechRecognition needs it)
    await navigator.mediaDevices.getUserMedia({ audio: true });
    micPermissionGranted = true;
  } catch (err) {
    if (err.name === 'NotAllowedError') {
      showMicBlockedHelp(voiceStatus);
    } else {
      voiceStatus.textContent = 'Microphone error: ' + err.message;
    }
    return;
  }

  const recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = 'en-US';

  // Store on window so stopVoice() can access it
  window._recognition = recognition;

  isRecording = true;
  collectedTranscripts = [];
  let currentInterim = '';

  micBtn.classList.add('recording-active');
  voiceStatus.textContent = 'Listening... speak your instructions';
  showTranscript();
  showVoiceControls(true);
  addTranscript('agent', 'Listening! Describe the presentation you want, then press stop.');

  recognition.onresult = (event) => {
    let interim = '';
    let finalText = '';

    for (let i = event.resultIndex; i < event.results.length; i++) {
      const transcript = event.results[i][0].transcript;
      if (event.results[i].isFinal) {
        finalText += transcript;
      } else {
        interim += transcript;
      }
    }

    // Show interim results in real-time
    if (interim) {
      currentInterim = interim;
      voiceStatus.textContent = interim;
    }

    // Collect final results
    if (finalText) {
      // addTranscript() handles pushing to collectedTranscripts
      addTranscript('user', finalText.trim());
      currentInterim = '';
      voiceStatus.textContent = 'Listening...';
    }
  };

  recognition.onerror = (event) => {
    console.error('Speech recognition error:', event.error);
    if (event.error === 'no-speech') {
      voiceStatus.textContent = 'No speech detected. Try again.';
    } else if (event.error === 'audio-capture') {
      voiceStatus.textContent = 'No microphone found.';
    } else if (event.error === 'not-allowed') {
      showMicBlockedHelp(voiceStatus);
    }
  };

  recognition.onend = () => {
    // SpeechRecognition can auto-stop; restart if still recording
    if (isRecording && !isPaused) {
      try { recognition.start(); } catch(e) {}
    }
  };

  recognition.start();
}
```

- [ ] **Step 2: Simplify `stopVoice()` — remove WebSocket/AudioWorklet cleanup**

Replace the `stopVoice()` function (currently at ~line 1093-1110) with:

```javascript
function stopVoice() {
  isRecording = false;
  isPaused = false;
  const micBtn = document.getElementById('micButton');
  micBtn.classList.remove('recording-active', 'recording-paused');
  document.getElementById('voiceStatusMessage').textContent = '';
  showVoiceControls(false);

  // Stop SpeechRecognition
  if (window._recognition) {
    window._recognition.stop();
    window._recognition = null;
  }
}
```

- [ ] **Step 3: Update `togglePause()` to pause/resume SpeechRecognition**

Replace the `togglePause()` function (currently at ~line 961-986) with:

```javascript
function togglePause() {
  const micBtn = document.getElementById('micButton');
  const voiceStatus = document.getElementById('voiceStatusMessage');
  const pauseIcon = document.getElementById('pauseIcon');
  const playIcon = document.getElementById('playIcon');

  if (isPaused) {
    // Resume
    isPaused = false;
    micBtn.classList.remove('recording-paused');
    micBtn.classList.add('recording-active');
    pauseIcon.style.display = '';
    playIcon.style.display = 'none';
    voiceStatus.textContent = 'Listening...';
    if (window._recognition) {
      try { window._recognition.start(); } catch(e) {}
    }
  } else {
    // Pause
    isPaused = true;
    micBtn.classList.remove('recording-active');
    micBtn.classList.add('recording-paused');
    pauseIcon.style.display = 'none';
    playIcon.style.display = '';
    voiceStatus.textContent = 'Paused';
    if (window._recognition) {
      window._recognition.stop();
    }
  }
}
```

- [ ] **Step 4: Remove unused state variables for WebSocket voice**

At the top of the `<script>` section (~line 660-668), remove these variables that are no longer needed for creation:

```javascript
// REMOVE these (no longer used for creation):
// let ws = null;
// let audioContext = null;
// let mediaStream = null;
// let workletNode = null;
// let playbackQueue = [];
// let isPlaying = false;
```

Also remove `isSendingAudio` (only used by the old creation `togglePause()` and `workletNode.port.onmessage`).

Keep: `isRecording`, `currentPresentationId`, `currentPresentationUrl`, `micPermissionGranted`, `collectedTranscripts`, `currentUser`, `isPaused`

Note: `ws`, `audioContext`, etc. are still needed for the **editing** flow. But the creation flow no longer uses them. The editing flow has its own prefixed versions (`editWs`, `editAudioContext`, etc.) so the creation ones can be removed.

**IMPORTANT:** Steps 1-5 of this task must be completed atomically (all in one pass) before testing. Removing variables (Step 4) before removing the functions that reference them (Step 5) would temporarily break the code. Execute all steps, then test.

- [ ] **Step 5: Remove `handleWebSocketMessage()` and audio playback functions for creation**

Remove these functions that are only used by the old bidi creation flow (~lines 1112-1188):
- `handleWebSocketMessage()` — the creation flow no longer uses WebSocket messages
- `queueAudioPlayback()` — no audio playback during creation
- `playNextAudio()` — no audio playback during creation
- `arrayBufferToBase64()` — keep this, it's used by editing
- `base64ToArrayBuffer()` — keep this, it's used by editing

- [ ] **Step 6: Verify `finishRecording()` works with new flow**

The `finishRecording()` function (~line 988-1042) is the critical bridge — it calls `stopVoice()`, joins `collectedTranscripts`, and sends them to `/generate`. Verify it still works:
- `stopVoice()` has been replaced (Step 2) — now stops SpeechRecognition instead of WebSocket
- `collectedTranscripts` is populated by `addTranscript()` which is called from `recognition.onresult`
- `fetch('/generate', ...)` is unchanged

No code changes needed — just verify the flow makes sense after Steps 1-5.

- [ ] **Step 7: Test voice creation manually**

1. Open `http://localhost:8000` in Chrome
2. Click the mic button
3. Speak "Create a 3-slide presentation about dogs"
4. Click stop
5. Verify: text goes to `/generate`, slides get created, preview appears

- [ ] **Step 8: Commit**

```bash
git add app/static/index.html
git commit -m "feat: replace bidi voice with SpeechRecognition for creation

Voice creation now uses browser-native SpeechRecognition API instead
of broken AudioWorklet + WebSocket bidi streaming. User speaks, text
is collected, then sent to /generate endpoint (which already works).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 3: Voice Editing + Model Update

### Task 4: Update voice model for editing

**Files:**
- Modify: `app/agent.py` (lines 629-630, 707-708)

- [ ] **Step 1: Update edit_agent model to latest**

In `app/agent.py`, change the edit_agent model (line ~708):

```python
# FROM:
model="gemini-2.5-flash-native-audio-preview-12-2025",
# TO:
model="gemini-2.5-flash-native-audio-latest",
```

Also update the voice `agent` model (line ~630) to match:

```python
# FROM:
model="gemini-2.5-flash-native-audio-preview-12-2025",
# TO:
model="gemini-2.5-flash-native-audio-latest",
```

- [ ] **Step 2: Tighten EDIT_INSTRUCTION to be less verbose**

In `app/agent.py`, replace the `EDIT_INSTRUCTION` string. Keep it concise — the editing agent should be fast and brief:

```python
EDIT_INSTRUCTION = """You are SlideMakr's voice editor. You modify presentations via spoken commands.

You have the full presentation state — all slides, elements, objectIds, and text.

When the user speaks a command:
1. Identify the element(s) by objectId from the state
2. Generate the Google Slides API request(s)
3. Call execute_slide_requests
4. Confirm briefly: "Done, changed the title."

Common edits:
- Change text: deleteText (type: ALL) then insertText
- Style text: updateTextStyle (fontSize, bold, foregroundColor, fontFamily)
- Background: updateSlideProperties with pageBackgroundFill
- Shape fill: updateShapeProperties with shapeBackgroundFill
- Add/remove: createShape/deleteObject
- Add slide: createSlide

Rules:
- Use ACTUAL objectIds from the state, never guess
- EMU: 1 inch = 914400. Slide = 9144000 x 5143500 EMU
- Colors: RGB 0.0-1.0
- Be brief. Confirm what you changed in one sentence.
- If ambiguous, ask a short question.
"""
```

- [ ] **Step 3: Verify server starts with new model**

```bash
python -c "from app.server import app; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add app/agent.py
git commit -m "feat: update voice model to latest, tighten edit instruction

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Task 5: Fix AudioWorklet processor name mismatch in editing

The editing flow in `index.html` uses `new AudioWorkletNode(editAudioContext, 'audio-processor')` (line ~1364) but the worklet file registers as `'pcm-capture'`. The creation flow had it right: `new AudioWorkletNode(audioContext, 'pcm-capture')` (line ~900).

**Files:**
- Modify: `app/static/index.html` (line ~1364)
- Read: `app/static/audio-processor.js` (to confirm processor name)

- [ ] **Step 1: Verify the registered processor name**

Read `app/static/audio-processor.js` and confirm the processor name in `registerProcessor()`.

- [ ] **Step 2: Fix the AudioWorkletNode name in editing code**

In `app/static/index.html`, in the `toggleEditVoice()` function (~line 1364):

```javascript
// FROM:
editWorkletNode = new AudioWorkletNode(editAudioContext, 'audio-processor');
// TO:
editWorkletNode = new AudioWorkletNode(editAudioContext, 'pcm-capture');
```

- [ ] **Step 3: Commit**

```bash
git add app/static/index.html
git commit -m "fix: correct AudioWorklet processor name in editing flow

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 4: Creation → SSO → Editing Flow

### Task 6: Wire the full creation → SSO → editing flow

The UI pieces exist but need to be connected properly. After creation, the user sees a preview with a "Login to get slides" button. After OAuth, the presentation is auto-claimed and the user can edit with voice.

**Files:**
- Modify: `app/static/index.html` (JS section)

- [ ] **Step 1: Store presentation_id in localStorage before OAuth redirect**

In `showPresentationLink()` (~line 1238), add localStorage persistence so the presentation_id survives the OAuth redirect:

```javascript
// At the start of showPresentationLink():
if (currentPresentationId) {
  localStorage.setItem('slidemakr_pending_presentation', currentPresentationId);
}
```

- [ ] **Step 2: Update the OAuth return handler to use localStorage**

Replace the `restoreFromUrl()` IIFE (~line 727-750) with a version that also checks localStorage:

```javascript
(function restoreAfterAuth() {
  const params = new URLSearchParams(window.location.search);
  const presIdFromUrl = params.get('presentation_id');
  const presIdFromStorage = localStorage.getItem('slidemakr_pending_presentation');
  const presId = presIdFromUrl || presIdFromStorage;

  if (presId) {
    currentPresentationId = presId;
    currentPresentationUrl = `https://docs.google.com/presentation/d/${presId}/edit`;

    // Wait for checkAuth to finish, then show preview and auto-claim
    setTimeout(async () => {
      showPresentationLink(currentPresentationUrl);

      // If logged in, auto-claim (share) the presentation
      if (currentUser) {
        try {
          await fetch('/api/claim-presentation', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ presentation_id: presId })
          });
          // Clear pending presentation
          localStorage.removeItem('slidemakr_pending_presentation');
        } catch (e) { console.log('Auto-claim failed:', e); }
      }

      // Clean up the URL
      if (presIdFromUrl) {
        window.history.replaceState({}, '', '/');
      }
    }, 600);
  }
})();
```

- [ ] **Step 3: Ensure "Edit with Voice" button only appears after login**

In `showPresentationLink()`, the logic already checks `currentUser` to decide which buttons to show. Verify that:
- Not logged in → shows "Login to get slides" button
- Logged in → shows "Open in Google Slides" + "Edit with Voice" buttons

This is already correct in the current code (~lines 1259-1281). No changes needed.

- [ ] **Step 4: Test the full flow manually**

1. Open `http://localhost:8000` (not logged in)
2. Type or speak a presentation request
3. Slides get created → preview shows → "Login to get slides" button appears
4. Click login → Google OAuth → redirects back
5. Presentation auto-claimed → "Open" + "Edit with Voice" buttons appear
6. Click "Edit with Voice" → editor opens with iframe + mic

- [ ] **Step 5: Commit**

```bash
git add app/static/index.html
git commit -m "feat: wire creation → SSO → editing flow with localStorage persistence

Presentation ID is stored in localStorage before OAuth redirect so it
survives the round-trip. On return, auto-claims the presentation and
shows editing options.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 5: Cleanup & Update Docs

### Task 7: Remove dead code from creation flow

Now that creation uses SpeechRecognition, the non-edit WebSocket/bidi path in the server is dead code for creation.

**Files:**
- Modify: `app/server.py` (voice runner section)

- [ ] **Step 1: Remove the non-edit voice runner**

In `app/server.py`, the `runner` (voice runner, ~lines 96-100) is no longer used for creation (SpeechRecognition → `/generate` handles it). The WebSocket `/ws` route still needs `edit_runner` for editing.

However, keep the `runner` for now — it could be useful if we want voice-only mode later. Instead, add a comment:

```python
# Voice runner — currently unused for creation (SpeechRecognition + /generate is used instead)
# Kept for potential future voice-only flows
runner = Runner(
    agent=agent,
    app_name=APP_NAME,
    session_service=session_service,
)
```

- [ ] **Step 2: Update the WebSocket route default behavior**

In `app/server.py`, the WebSocket `/ws` route (~line 239) currently defaults to `runner` when no `presentation_id` is provided. Since creation no longer uses this path, update the default to reject non-edit connections:

In the WebSocket handler (~line 263-264):

```python
# Check if this is an editing session
presentation_id = ws.query_params.get("presentation_id")
if not presentation_id:
    await ws.send_json({"type": "error", "message": "presentation_id required for editing"})
    await ws.close()
    return

is_edit_mode = True  # All /ws connections are now editing sessions
active_runner = edit_runner
```

Note: Keep `is_edit_mode` variable since it's referenced by the `logger.info()` on the next line.

- [ ] **Step 3: Commit**

```bash
git add app/server.py
git commit -m "refactor: mark voice runner as unused, require presentation_id for /ws

Creation now uses SpeechRecognition + /generate. WebSocket /ws is only
for voice editing and requires a presentation_id.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Task 8: Update CLAUDE.md to reflect current architecture

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rewrite CLAUDE.md to match current architecture**

Replace the entire contents of `CLAUDE.md` with:

```markdown
# SlideMakr

AI agent that creates and edits Google Slides presentations from voice and text,
using Google ADK + Gemini 2.5 Flash and the Google Slides/Drive APIs.

## Commands

\`\`\`bash
# Start dev server
source slidemakr-venv/bin/activate
uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload

# Install dependencies
pip install -r requirements.txt
\`\`\`

## Architecture

\`\`\`
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
  studio/               # Legacy LangGraph agents (v1=buggy, v2=working, unused)
  slidemakr-venv/       # Primary virtualenv
\`\`\`

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

\`\`\`
GOOGLE_API_KEY=...              # Gemini API key
SERVICE_ACCOUNT_PATH=...        # Google service account JSON (for Slides/Drive API)
GOOGLE_CLOUD_PROJECT=slidemakr  # Firestore project
GOOGLE_OAUTH_CLIENT_ID=...      # OAuth (optional for local dev)
GOOGLE_OAUTH_CLIENT_SECRET=...  # OAuth (optional for local dev)
\`\`\`

## Key Details

- text_agent uses `gemini-2.5-flash` for reliable tool calls via /generate
- edit_agent uses `gemini-2.5-flash-native-audio-latest` for bidi voice editing
- execute_slide_requests separates structural (createSlide, createShape) from content
  requests — structural batched together, content one-by-one for error isolation
- Firestore has in-memory fallback so local dev works without GCP credentials
- AudioWorklet processor registers as 'pcm-capture' (audio-processor.js)
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md to reflect ADK architecture

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

### Task 9: Update PROJECT_PLAN.md priority order

**Files:**
- Modify: `PROJECT_PLAN.md`

- [ ] **Step 1: Update the priority order section at the bottom**

Replace the "Priority Order (Suggested)" section with the user's preferred order:

```markdown
## Priority Order

1. **Feature 4 — Comment resolution** (power feature, auto-resolve Google Slides comments)
2. **Feature 7 — Wait experience timer** (engagement + virality play)
3. **Feature 6 — Brand theming** (partially done — web search tool added)
4. **Feature 2 — Stripe** (monetization — pay-per-slide credits)
5. **Feature 1 — RAG fix** (may be less urgent if current approach works)
```

- [ ] **Step 2: Commit**

```bash
git add PROJECT_PLAN.md
git commit -m "docs: update feature priority order per user preference

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```
