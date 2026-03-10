# SlideMakr — Project Plan

> **Vision:** Build slides with voice. A reliable, monetized AI agent that creates,
> edits, and manages Google Slides presentations through natural language and voice.

---

## Feature Roadmap

### 1. RAG-Based Request Generation (Fix & Maintain)

**Problem:** The current approach asks the LLM to write raw Google Slides API JSON,
which is error-prone. A RAG database of known-good request formats exists but isn't
working well.

**Tasks:**
- [ ] Audit the current RAG setup — identify why retrieval is failing (embedding quality? chunk size? query mismatch?)
- [ ] Fix retrieval so the agent pulls correct request templates instead of hallucinating JSON
- [ ] Build a pipeline to continuously update the RAG database with new Google Slides API request formats
- [ ] Add validation: compare generated requests against the RAG schema before calling `batchUpdate`
- [ ] Test end-to-end: user prompt → RAG retrieval → valid API requests → working slides

**Key question:** Where is the RAG database currently hosted? (Needs codebase investigation — may be on Replit)

---

### 2. Stripe Checkout — Pay-Per-Slide Credits

**Flow:**
1. User opens app → gets **first slide deck free**
2. After that, user buys **credits** (each credit = one slide)
3. Stripe Checkout handles payment

**Tasks:**
- [ ] Set up Stripe account + API keys
- [ ] Create credit packages (e.g., 10 slides / 50 slides / 100 slides)
- [ ] Build Stripe Checkout session creation endpoint
- [ ] Add webhook handler for `checkout.session.completed` to credit the user's account
- [ ] Track credits per user (database: credits remaining, credits used)
- [ ] Gate slide creation: check credits before `create_presentation_tool` runs
- [ ] Handle the "first deck free" logic (flag per user account)
- [ ] Add a simple billing/credits UI

---

### 3. Real-Time Voice Editing

**Concept:** While viewing a presentation, the user speaks commands like
*"change this to blue"*, *"make the title bigger"*, and the agent executes them live.

**Tasks:**
- [ ] Integrate speech-to-text (Web Speech API / Whisper / Deepgram)
- [ ] Build a "voice command" parsing layer that maps spoken instructions to Google Slides API requests
- [ ] Implement real-time connection to the active presentation (track current slide + selected element)
- [ ] Execute edits via `batchUpdate` and reflect changes live
- [ ] Handle ambiguity ("this" → which element? needs slide context awareness)
- [ ] Latency optimization — voice → edit should feel instant

**Dependencies:** Feature 1 (RAG) makes this more reliable since voice commands map to known request templates.

---

### 4. Auto-Resolve Comments

**Concept:** User says *"address and resolve all comments"* and the agent reads all
comments on the presentation, makes the requested changes, and resolves them.

**Tasks:**
- [ ] Use Google Slides API to fetch all comments on a presentation
- [ ] Parse each comment into an actionable edit instruction
- [ ] Execute edits per comment, then mark comment as resolved
- [ ] Add context/preferences layer (future):
  - Answer all vs. filter by commenter
  - User-defined response style ("typically I would...")
  - Smart prioritization based on comment content

**Dependencies:** Feature 3 (voice editing infrastructure) shares the same edit execution pipeline.

---

### 5. SSO + Google Drive Integration

**Concept:** Users sign in via SSO. With their permission (toggle), the agent accesses
their Google Drive to use existing templates, find old slides, and reuse content.

**Tasks:**
- [ ] Implement SSO (Google OAuth 2.0 — aligns with existing Google API usage)
- [ ] Add a Drive access toggle (opt-in permission scope)
- [ ] Build Drive indexing: scan user's presentations for templates and content
- [ ] Template matching: when creating new slides, suggest/use the user's existing templates
- [ ] Slide reuse: find and copy slides from older presentations based on user prompt
- [ ] Version lookup: "find the version of X presentation from last month"

**Note:** Currently using a service account — SSO shifts to per-user OAuth, which is a
significant auth architecture change.

---

### 6. Web Search — Company Brand Theming

**Concept:** When creating slides for a company, the agent searches the web for that
company's brand guidelines and automatically themes the presentation to match — using
their colors, fonts, logo, and visual style.

**Tasks:**
- [x] Add `search_company_branding` tool that uses Gemini + Google Search grounding
- [x] Update agent instruction with branded presentation workflow
- [ ] Add logo placement logic (title slide + optional watermark on all slides)
- [ ] Build a brand cache so repeated requests for the same company skip the search
- [ ] Support "retheme" — apply a company's brand to an existing presentation
- [ ] Test with 10+ companies (Scale AI, Stripe, Airbnb, etc.) and verify color accuracy

**Priority:** Soon
**Added:** 2026-02-25

---

### 7. Slide Creation Wait Experience — "Time Saved" Timer

**Concept:** While the user waits for their slides to be created, show an engaging
experience: ask "How much time do you waste on making slides?", run a timer measuring
how long creation takes, then show a sarcastic/quirky summary like "4.8 hours saved —
in that time you could: play a sport, hang out with your kids (who are we kidding),
get drunk at a bar" with a funny randomized list.

**Tasks:**
- [ ] Add "How much time do you waste on slides?" prompt with quick-select options (1hr, 3hr, 5hr, "my whole life")
- [ ] Build a visible timer that counts up during slide creation
- [ ] Create a pool of 20+ sarcastic/quirky "things you could do instead" items
- [ ] On completion, show a "Time Saved" card with the timer result and 3-4 random funny suggestions
- [ ] Add subtle animation/confetti when slides are done
- [ ] Track cumulative time saved across sessions (persist in localStorage or DB)
- [ ] A/B test: does this increase sharing / return usage?

**Priority:** Soon
**Added:** 2026-02-25

---

## Open Questions

### Hosting: Replit vs. Self-Hosted

**Current state:** Prototype is on Replit. You have a year of free Replit hosting.

**Considerations:**

| Factor | Replit | Self-Hosted (e.g., Railway, Fly.io, Vercel) |
|--------|--------|----------------------------------------------|
| Cost | Free for 1 year | Paid from day 1 |
| Speed to test traction | Fast — already deployed | Migration overhead |
| Developer experience | You dislike it | Full control |
| Scaling | Limited | Flexible |
| Stripe/webhooks | Works fine | Works fine |
| Custom domain/SSO | Supported | Supported |

**Recommendation:** Keep Replit for now to validate traction (it's free and deployed).
Develop locally with Claude Code, push to GitHub, and deploy to Replit from there.
Migrate hosting only when you hit Replit's limits or are ready to scale.

### GitHub Access for Claude Code

To give Claude Code access to your GitHub repo:

```bash
# 1. Make sure gh CLI is installed
brew install gh

# 2. Authenticate
gh auth login

# 3. Then Claude Code can push, create PRs, etc.
```

If the repo is on Replit's Git, you can add a GitHub remote:
```bash
git remote add github https://github.com/YOUR_USERNAME/SlideMakr.git
git push github main
```

---

## Priority Order

1. **Feature 4 — Comment resolution** (power feature, auto-resolve Google Slides comments)
2. **Feature 7 — Wait experience timer** (engagement + virality play)
3. **Feature 6 — Brand theming** (partially done — web search tool added)
4. **Feature 2 — Stripe** (monetization — pay-per-slide credits)
5. **Feature 1 — RAG fix** (may be less urgent if current approach works)
