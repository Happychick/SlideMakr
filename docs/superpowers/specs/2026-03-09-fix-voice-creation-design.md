# Fix Voice Creation + Enable Voice Editing

**Date:** 2026-03-09
**Status:** Approved

## Problem

The bidi-streaming voice agent (`gemini-2.5-flash-native-audio`) is broken for creation — it can't understand audio input and dumps reasoning instead of acting. The text endpoint `/generate` works. Meanwhile, the other chat added features (auth, branding, edit agent, text agent) but went off-plan by wiring them in prematurely, overcomplicating the flow.

## Approach

**Approach A: Simplify the flow, keep the code.** Commit existing changes as-is (preserving all work), then refactor so the homepage is simple: mic → speak → slides appear → login → edit with voice.

## Design

### Step 1: Commit & Stabilize

- Commit all current uncommitted changes (auth.py, branding tool, edit agent, text agent, expanded server) as a safety net
- Verify `/generate` still works — fix any import/startup issues from the other chat's changes

### Step 2: Voice Creation (frontend only)

- Replace the `AudioWorklet` + WebSocket approach for **creation** with browser-native `SpeechRecognition` API
- Flow: tap mic → real-time transcript appears → tap done → text goes to `/generate`
- Fallback: text input if `SpeechRecognition` not supported (notably Firefox, some mobile browsers)
- No backend changes — `/generate` already works
- Remove/deprecate the non-edit bidi WebSocket path for creation (the `runner`/`agent` voice runner becomes unused for creation — clean up dead code in `server.py`)

### Step 3: Voice Editing (keep bidi WebSocket)

- Keep bidi WebSocket for editing — real-time voice matters here ("change the title to blue")
- Update voice model from `gemini-2.5-flash-native-audio-preview-12-2025` to `gemini-2.5-flash-native-audio-latest`
- `/ws?presentation_id=X` route and `edit_runner` already exist
- Editor UI (iframe + mic + transcript) already exists in index.html
- Tighten `EDIT_INSTRUCTION` to be less verbose

### Step 4: Connect Creation → Editing Flow

- After `/generate` creates slides, show presentation URL + "Edit with Voice" button
- Clicking it opens editor view with presentation in iframe + bidi voice connection

### Step 5: SSO Before Editing

- SSO gate before entering edit mode (not before creation)
- Flow: slides created (no login) → "Sign in with Google to save & edit" → OAuth → auto-share presentation to user's Drive → enter edit mode
- `auth.py` already built (login, callback, logout, /auth/me)
- Auto-share logic already in `server.py`
- `/api/presentations` and `/api/claim-presentation` already exist
- Frontend needs: "Sign in with Google" button after creation, store `presentation_id` in localStorage before redirect, claim after return

### Data Layer

Already built in `db.py` (Firestore with in-memory fallback):

| Collection | Purpose |
|---|---|
| `presentations` | presentation_id, title, user_id, url, status |
| `slide_errors` | request_json, error_message, retry tracking |
| `audio_log` | user_id, session_id, transcripts |
| `user_memory` | session summaries, preferences |
| `users` | google_id, email, name, picture, refresh_token |

## Future Features (sequenced, separate cycles)

1. **Comment resolution** — auto-resolve Google Slides comments
2. **Wait experience** — "time saved" timer while slides generate
3. **Branding** — company-themed slides (tool already built, needs wiring)
4. **Stripe** — pay-per-slide credits (first deck free)
5. **RAG fix** — improve slide API request reliability

Each gets its own design → plan → implement cycle.

## Out of Scope (this spec)

- Chat/assistant experience (future — responding to feedback)
- Stripe payments
- RAG database improvements
- Deployment/hosting changes
