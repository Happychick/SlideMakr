# DevPost Submission - SlideMakr

## Title
SlideMakr - Create & Edit Google Slides with Your Voice

## Tagline
AI agent that turns voice commands into professional presentations using Google ADK + Gemini 2.5 Flash

## Category
Live Agents

---

## Inspiration

Creating slide decks is one of the most time-consuming tasks in any workplace. You know what you want to say, but translating ideas into formatted slides takes hours of dragging, typing, and tweaking. We asked: what if you could just *say* what you want and have an AI agent build it for you in seconds?

## What it does

SlideMakr is a voice-powered AI agent that creates and edits Google Slides presentations through natural conversation.

**Create:** Speak or type your presentation idea -- "Make a 5-slide pitch deck about sustainable energy with charts and a flowchart" -- and SlideMakr generates a complete, styled Google Slides deck with titles, bullet points, images, data charts, and flowcharts.

**Edit:** After creation, click "Edit with Voice" to have a real-time conversation with the AI. Say things like "make the title font bigger," "add a pie chart on slide 3," or "create a flowchart showing our process" -- and watch the changes happen live in the embedded preview.

**Share:** Sign in with Google to automatically save presentations to your Drive and share them.

Key capabilities:
- Voice-to-slides creation (full presentations in seconds)
- Real-time voice editing with bidirectional audio streaming
- Auto-layout flowcharts (vertical, horizontal, and auto-detect)
- Data charts (bar, line, pie, doughnut, radar)
- AI-powered image search and placement
- Company brand theming (colors, fonts, logo)
- Google OAuth integration for Drive sharing

## How we built it

We iterated through two architectures -- starting with LangGraph + OpenAI, then rebuilding on Google ADK + Gemini for better native audio streaming and tool-calling reliability. The final system is built entirely on Google ADK + Gemini.

**Google ADK (Agent Development Kit)** is the backbone. We use two specialized agents:

1. **text_agent** (Gemini 2.5 Flash) -- Handles presentation creation. Takes natural language and generates Google Slides API `batchUpdate` requests. Equipped with tools for creating slides, flowcharts, charts, image search, and brand theming.

2. **edit_agent** (Gemini 2.5 Flash Native Audio) -- Handles real-time voice editing via ADK's bidirectional streaming. Receives raw PCM audio, understands the command, reads current slide state, generates edit requests, and responds with spoken confirmation.

The agent tools map directly to Google APIs:
- `execute_slide_requests` -- sends `batchUpdate` to the Slides API
- `get_presentation_state` -- reads current slide structure
- `create_flowchart` -- our BFS-based layout engine that auto-positions nodes and connectors
- `create_chart` -- generates chart images via QuickChart
- `search_web_image` -- finds relevant images via Unsplash
- `search_company_branding` -- uses Gemini + Google Search to find brand colors/fonts

**Smart batch execution:** We separate structural requests (createSlide, createShape) from content requests (insertText, updateTextStyle). Structural requests run as one batch; content requests run one-by-one. This prevents a single bad text insertion from blocking an entire 10-slide presentation.

**Frontend:** Vanilla HTML/JS with the Web Speech API for creation and an AudioWorklet processor for voice editing (16kHz PCM capture, 24kHz playback).

**Infrastructure:** FastAPI backend deployed on Cloud Run with Docker. Secrets managed via GCP Secret Manager. Firestore for persistence (with in-memory fallback for local dev).

## Challenges we ran into

- **Reliable slide generation:** Getting Gemini to generate valid Google Slides API JSON consistently was the biggest challenge. We solved it with detailed examples in the agent instructions and error-isolating batch execution.
- **Flowchart layout:** Auto-positioning nodes on a fixed-size slide required a BFS-based graph traversal algorithm with overflow detection. We built three layout engines (vertical, horizontal, auto-detect) to handle different graph shapes.
- **Voice editing latency:** Bidirectional audio streaming through ADK's `LiveRequestQueue` required careful buffer management and PCM encoding to feel responsive.
- **Error isolation:** A single invalid request in a batch of 30 would fail the entire batch. Our smart execution strategy separates structural and content requests to isolate failures.

## Accomplishments that we're proud of

- Creating a complete 5-slide presentation with charts and flowcharts takes under 30 seconds
- The voice editor feels like talking to a colleague -- say "make it blue" and it just works
- The flowchart engine handles complex multi-level diagrams with automatic connector routing
- Zero manual JSON editing -- the agent translates natural language to API calls end-to-end

## What we learned

- Google ADK's bidirectional streaming with Gemini Native Audio is incredibly powerful for real-time agent interactions
- Detailed tool descriptions and examples in agent instructions dramatically improve tool-calling reliability
- Error isolation patterns (batch structural, one-by-one content) are essential for production-grade agent tools
- The Google Slides API is remarkably expressive -- almost any visual design is possible through `batchUpdate`

## What's next for SlideMakr

- **Comment resolution** -- Auto-resolve Google Slides comments via voice
- **Wait experience** -- Show "time saved" timer during creation
- **Brand theming v2** -- Full brand kit import (logo placement, color schemes, font pairing)
- **Stripe integration** -- Pay-per-slide credits for premium features
- **Collaborative editing** -- Multiple users editing the same deck via voice

---

## Built With
- google-adk
- gemini-2.5-flash
- google-slides-api
- google-drive-api
- fastapi
- python
- cloud-run
- docker
- firestore
- javascript
- websockets

## Links
- **Live demo:** https://slidemakr-gj4eorwgtq-uc.a.run.app
- **GitHub:** https://github.com/Happychick/SlideMakr
- **Demo video:** [YouTube link - to be added]
