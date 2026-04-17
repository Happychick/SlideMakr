# SlideMakr — Project Plan

> **Vision:** Beautiful, relevant slides created in minutes. A reliable AI agent that
> creates, edits, and manages Google Slides presentations through voice and text.

---

## Global Rule — Preview Verification Gate

After every edit to `app/*.py` or `app/static/index.html`:
1. `preview_start` with config "SlideMakr ADK Server" (`.claude/launch.json`, uvicorn :8080 `--reload`)
2. Reload if HMR doesn't fire: `preview_eval` → `window.location.reload()`
3. `preview_console_logs level=error` — must be clean
4. `preview_logs level=error` — must be clean
5. Frontend: `preview_snapshot` or `preview_inspect` to confirm state
6. Flow: `preview_click` / `preview_fill` to drive, then re-check

Skip only for non-browser changes (pure tooling/tests/types).
Never ask the user to test manually — verify and show proof.

---

## Completed

### Voice/Text Creation ✅
Full slide creation from voice or text input via text_agent (Gemini 2.5 Flash).
Supports: titles, bullet points, images (Unsplash), charts (QuickChart), flowcharts (BFS layout engine).

### Voice Editing ✅
Real-time conversational editing via edit_agent (Gemini 2.5 Flash Native Audio).
Bidirectional audio streaming through ADK LiveRequestQueue.

### Google OAuth + Drive Sharing ✅
Sign in with Google → auto-share presentations to Drive. Testing mode (manual test users).

### Smart Batch Execution ✅
Structural requests batched, content requests one-by-one for error isolation.
`slides_schema.py` auto-validates and fixes common API mistakes.

### Brand Theming v1 ✅
`search_company_branding` tool uses Gemini + Google Search to find brand colors/fonts.
Agent instruction chains branding into creation flow.

### Cloud Run Deployment ✅
Docker → Cloud Build → Cloud Run. Secrets via GCP Secret Manager. Live and working.

### DevPost Submission ✅
Submitted to Gemini Live Agent Challenge (Mar 16, 2026).

---

## Active: Agent Quality System

Building a quality system so the agent gets BETTER over time through three feedback loops:
1. **Error learning** — every API error logged, auto-fix patterns → error rate → 0
2. **User feedback** — formatting preferences encoded as hard constraints
3. **Audience feedback** — comment resolution feeds back into creation quality

### Phase 1: Metrics + Error Learning (Step 2)
- Instrument every creation with timing + error tracking
- Error pattern database: recurring errors get auto-fixes in `validate_requests()`
- `GET /metrics` dashboard endpoint
- Async post-creation visual review (background, non-blocking)
- **Files**: server.py, slidemakr.py, slides_schema.py, db.py

### Phase 2: Arena Constraints (Step 3)
- Bounds checking: reject off-slide elements
- Template protection: don't reposition placeholders
- Overlap detection: warn when text overlaps shapes
- Layout recipes: validated positioning constants from good slides
- **Files**: slides_schema.py, slidemakr.py, agent.py

### Phase 3: Eval Pipeline (Step 4)
- 5 standard eval prompts → real presentations → scored (0-1)
- Dimensions: completeness, error rate, visual quality, speed, content richness
- `POST /admin/run-eval`, `GET /admin/eval-history`
- **Files**: NEW app/eval.py, db.py, server.py

### Phase 4: Context Rot + User Learning (Step 5)
- Turn counter in /ws — re-inject state every 5 tool calls
- User preference learning from creation history
- Pre-fill style preferences in agent prompt for repeat users
- **Files**: server.py, db.py

### Quality SLAs

| Metric | Target | Trend |
|--------|--------|-------|
| Creation time (4 slides) | < 30s | Faster with user history |
| Error rate | < 10% | → 0% over time |
| Visual quality | "good" 80%+ | → 95%+ with learning |
| Completeness | 100% requested | Maintained |
| Eval suite overall | > 0.7 | → 0.9+ |

---

## Next: Feature Roadmap

### Step 6: Brand Theming v2
Logo placement, auto-apply colors/fonts to backgrounds and text, brand cache, "retheme" existing presentations.
**Files**: agent.py, slidemakr.py, db.py

### Step 7: Edit Existing Presentations (Drive Picker)
List user's Google Slides from Drive, import flow, share with service account for editing.
**Files**: slidemakr.py, auth.py, server.py, index.html, db.py

### Step 8: Comment Resolution
Fetch comments via Drive API, parse into edits, execute and resolve.
**Files**: slidemakr.py, agent.py, db.py

### Step 9: Wait Experience — "Time Saved" Timer
- Pre-creation onboarding question (once per browser, `localStorage.usualSlideTime`):
  "How long does it usually take you to make slides?" (30min / 1hr / 3hr / "my whole life")
- Live count-up timer during creation (0.0s, 0.1s, …) via `requestAnimationFrame`
- Completion card: "Created [N] slides in [X] seconds. You saved ~[Y] hours. In that time
  you could [funny suggestion]." (20+ suggestions pool)
- Confetti burst on completion (CSS/JS, no library)
- **NEW:** cumulative `users.total_time_saved_seconds` in Firestore + header badge "2h 34m saved"
**Files**: index.html, server.py (return `duration_seconds` from Phase 1), db.py

### Step 10: Speed / Multi-Agent
Split into content_agent + visual_agent + design_agent running in parallel.
Target: 2x faster (15s for 4-slide deck).
**Files**: agent.py, server.py

### Step 11: Stripe Checkout — Pay-Per-Slide Credits

Uses Stripe's **hosted Checkout via CheckoutSessions API** (secure prebuilt page, per
`stripe:stripe-best-practices` skill). Turn on dynamic payment methods in Stripe Dashboard so
Stripe auto-picks payment methods per region.

Packages: 10 credits/$4.99, 50/$19.99, 100/$29.99. First deck free per user.

**Endpoints**:
- `POST /billing/checkout` → creates CheckoutSession, returns `session.url` for redirect
- `POST /billing/webhook` → verifies signature, credits user on `checkout.session.completed`
- `GET /billing/credits` → remaining credits
- `/generate` + `/generate-audio` gated: `402 {checkout_required: true}` when out of credits

**Dev vs prod test matrix**:
- **Dev:** `STRIPE_SECRET_KEY=sk_test_…`, `stripe listen --forward-to localhost:8080/billing/webhook`
- **Test cards** (`stripe:test-cards` skill): `4242 4242 4242 4242` success, `4000 0000 0000 0002` declined, `4000 0025 0000 3155` auth required
- **Prod:** `STRIPE_SECRET_KEY_LIVE` in GCP Secret Manager, `deploy.sh` wires secret, live webhook URL on Cloud Run
- Run [Stripe Go Live Checklist](https://docs.stripe.com/get-started/checklist/go-live.md) before enabling live keys

**Test matrix**: free first deck | buy 10 | webhook delivered | deduct on `/generate` | declined card | session expiry

**Files**: NEW `app/stripe_billing.py`, `server.py`, `db.py` (users.credits schema), `index.html`, `deploy.sh`, `requirements.txt` (`stripe>=13`)

### Step 12: Google Slides Add-on — Voice From Inside Google Slides

User insight: users want to stay in Google Slides and talk to SlideMakr, not switch apps.
Build a Google Workspace Add-on (Apps Script + HTML sidebar) that calls the existing backend.

**New directory**: `slides_addon/`
- `appsscript.json` — manifest declaring Slides add-on + OAuth scopes
- `Code.gs` — `onOpen(e)` installs "SlideMakr" menu → opens sidebar; `getActivePresentation().getId()` for context
- `Sidebar.html` — mic (MediaRecorder) + transcript + "Connect SlideMakr" auth

**Auth**: short-lived token via NEW `POST /api/addon-token` (`app/auth.py` + `app/server.py`).
Sidebar opens popup to SlideMakr OAuth, receives token via `window.postMessage`, stored in
`PropertiesService.getUserProperties()`.

**Voice flow**: MediaRecorder blob → `POST /generate-audio` (exists from mobile fix) with
active `presentation_id` → `edit_agent` in Drive mode via contextvars → user sees live updates
in their open Slides tab (no iframe — they're already looking at the deck).

**Distribution**: private Workspace Marketplace listing for beta → public after LAUNCH.md security posture done.

**Files**: NEW `slides_addon/{appsscript.json, Code.gs, Sidebar.html}`, `server.py`, `auth.py`

### Step 13: PowerPoint Office Add-in

Same voice-editing UX inside PowerPoint Desktop + PowerPoint for Web (fast-follow to Step 12).

**New directory**: `powerpoint_addon/`
- `manifest.xml` — Office add-in manifest
- TypeScript + Office.js task pane with mic + transcript + auth (reuses `/api/addon-token`)

**Backend adaptation**: NEW `POST /api/edit-pptx` accepts PPTX upload, round-trips through
Google Slides (convert → edit with existing tools → convert back) to reuse all slide tooling.
Native python-pptx editing is faster but needs new tool set — defer to v2.

**Distribution**: Microsoft Partner Center → AppSource (5–10 business day verification).

**Files**: NEW `powerpoint_addon/{manifest.xml, src/taskpane.ts, src/taskpane.html}`, `server.py`

### Step 15: Tool Decomposition for Voice Editing ⚠ BLOCKER

Replace the single `execute_slide_requests(presentation_id, requests: List[Dict])`
tool with ~20 narrow typed tools (`add_text_box`, `update_text`, `set_element_color`,
etc.) so hallucinated Slides API shapes are structurally impossible and native-audio
Gemini Live doesn't crash with 1011 after the first tool call.

**Why this blocks Stripe:** voice editing is the core product. Voice editing currently
crashes when the agent tries to emit a slide-modification tool call — the 19 KB typed
`any_of` schema from the earlier attempt was too large for native-audio Gemini Live.
See [HANDOFF.md](HANDOFF.md) for the design, test plan, and log evidence.

**Design:**
- ~20 narrow tools, one per Slides API operation (use existing Pydantic wrappers from
  [app/slides_schema.py](app/slides_schema.py) as the source of truth)
- Gemini's parallel function calling: LLM emits multiple tool calls in one turn
- Server-side: each tool APPENDs to a session-scoped batch buffer, a final
  `commit_edits()` tool flushes to ONE `batchUpdate` HTTP call to Google

**Files:** `agent.py` (major), `slides_schema.py` (reuse wrappers), possibly `slidemakr.py`
**Verify:** end-to-end voice edit a Drive deck with ≥10 consecutive edits, no 1011/1007.

### Step 14: Session History & Recovery

Users get interrupted mid-creation. Need to resume with full context (from FUTURE.md).

- Left navbar: all past sessions (title + timestamp + thumbnail)
- Guest (not signed in): `sessionStorage` only, cleared on tab close
- Signed in: Firestore-backed, cross-device
- Endpoints: `GET /api/sessions`, `GET /api/sessions/{id}` (full chat history + presentation_id)
- Link each session to the creation conversation (already stored per Phase 4 context-rot work)
- Click a session → load presentation in iframe + chat history → resume editing

**Files**: index.html, server.py, db.py

---

## Execution Priority

Sequential numbering above ≠ execution order. User-specified priority for launch:

```
A (HANDOFF.md fixes) ✅ A1 A2 A3 done
  ↓
Step 15 (Tool Decomposition)  ← CURRENT BLOCKER — voice editing must work before Stripe
  ↓
Step 11 (Stripe) → Step 9 (Wait UX) → Step 12 (Google Slides Add-on)
  → Step 13 (PowerPoint) → Step 14 (Sessions) → Step 8 (Comments) → Step 10 (Speed)
```

**Step 15 rule:** Stripe is blocked until voice editing works end-to-end
(open Drive deck → voice edit → changes apply → no 1011 crashes). See
[HANDOFF.md](HANDOFF.md) for the design + test plan.

GTM (TikTok, LinkedIn, blog, Clay outreach, enterprise contracts, security posture,
meeting-MCP recipe) lives in `LAUNCH.md` and runs in parallel with Step 12 onward.

---

## Idea Inbox

See `FUTURE.md` for captured feature ideas not yet prioritized.
See `LAUNCH.md` for GTM plan (created when Step 11 starts).
