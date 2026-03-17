# SlideMakr — Project Plan

> **Vision:** Beautiful, relevant slides created in minutes. A reliable AI agent that
> creates, edits, and manages Google Slides presentations through voice and text.

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
Pre-creation question, live count-up timer, completion card with funny suggestions, confetti.
**Files**: index.html, server.py, db.py

### Step 10: Speed / Multi-Agent
Split into content_agent + visual_agent + design_agent running in parallel.
Target: 2x faster (15s for 4-slide deck).
**Files**: agent.py, server.py

### Step 11: Stripe — Pay-Per-Slide Credits
Credit packages, Stripe Checkout, webhooks, first deck free, billing UI.
**Files**: NEW stripe_billing.py, server.py, db.py, index.html

---

## Idea Inbox

See `FUTURE.md` for captured feature ideas not yet prioritized.
