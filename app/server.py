"""
SlideMakr - FastAPI WebSocket Server

Serves the voice UI and handles real-time audio streaming via WebSocket.
Uses Google ADK's bidi-streaming for voice interaction with Gemini.

Endpoints:
- GET /           → serves frontend (index.html)
- GET /static/... → serves static files (JS, CSS)
- WS  /ws         → WebSocket for voice streaming
- POST /generate  → text-based slide generation (fallback)
"""

import asyncio
import base64
import json
import logging
import os
import traceback
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load .env from app/ directory or project root
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()  # fallback to project root

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.agents.run_config import RunConfig
from google.genai import types

from .agent import agent, text_agent, edit_agent
from .auth import router as auth_router, get_current_user
from . import db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# APP SETUP
# ============================================================================

app = FastAPI(title="SlideMakr", version="1.0.0")

# Session middleware (must be added before CORS)
SESSION_SECRET = os.getenv("SESSION_SECRET_KEY", "slidemakr-dev-secret-change-in-prod")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth routes
app.include_router(auth_router)


@app.middleware("http")
async def add_permissions_policy(request, call_next):
    """Add Permissions-Policy header to allow microphone access."""
    response = await call_next(request)
    response.headers["Permissions-Policy"] = "microphone=*, camera=()"
    return response


# Static files
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ============================================================================
# ADK RUNNER SETUP
# ============================================================================

APP_NAME = "slidemakr"
session_service = InMemorySessionService()

# Voice runner — uses native audio model for bidi-streaming
runner = Runner(
    agent=agent,
    app_name=APP_NAME,
    session_service=session_service,
)

# Text runner — uses standard model for reliable tool calls via POST /generate
text_runner = Runner(
    agent=text_agent,
    app_name=APP_NAME,
    session_service=session_service,
)

# Edit runner — uses native audio model for real-time voice editing
edit_runner = Runner(
    agent=edit_agent,
    app_name=APP_NAME,
    session_service=session_service,
)

# ============================================================================
# ROUTES
# ============================================================================


@app.get("/")
async def serve_frontend():
    """Serve the main frontend page."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"message": "SlideMakr API is running. Frontend not found."})


@app.get("/health")
async def health_check():
    """Health check for Cloud Run."""
    return {"status": "healthy", "service": "slidemakr"}


# ============================================================================
# TEXT-BASED GENERATION (SSE streaming)
# ============================================================================


@app.post("/generate")
async def generate_from_text(request: Request):
    """Generate a presentation from text instructions.

    Uses text_runner (standard Gemini model) for reliable tool calls.
    """
    body = await request.json()
    text = body.get("text", "")
    if not text:
        return JSONResponse({"success": False, "error": "No text provided"}, status_code=400)

    # Use authenticated user_id if logged in
    current_user = get_current_user(request)
    if current_user:
        user_id = current_user["google_id"]
    else:
        user_id = body.get("user_id", f"user_{uuid.uuid4().hex[:8]}")

    logger.info(f"/generate called: user={user_id}, text={text[:100]}...")

    try:
        session = await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
        )

        final_response = ""
        presentation_url = None
        presentation_id = None

        content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=text)]
        )

        async def run_agent():
            nonlocal final_response, presentation_url, presentation_id
            event_count = 0
            async for event in text_runner.run_async(
                user_id=user_id,
                session_id=session.id,
                new_message=content,
            ):
                event_count += 1
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            final_response += part.text
                            logger.info(f"/generate event #{event_count}: text={part.text[:80]}")
                        if part.function_call:
                            logger.info(f"/generate event #{event_count}: tool_call={part.function_call.name}")
                        if part.function_response:
                            resp_data = part.function_response.response
                            if isinstance(resp_data, dict):
                                logger.info(f"/generate event #{event_count}: tool_response keys={list(resp_data.keys())}")
                                if 'url' in resp_data:
                                    presentation_url = resp_data['url']
                                if 'presentation_id' in resp_data:
                                    presentation_id = resp_data['presentation_id']
            logger.info(f"/generate complete: {event_count} events, url={presentation_url}")

        await asyncio.wait_for(run_agent(), timeout=180)

        # Auto-share with logged-in user so it appears in their Drive
        if current_user and presentation_id:
            try:
                from . import slidemakr as sm
                sm.share_presentation(presentation_id, current_user["email"])
                logger.info(f"Auto-shared {presentation_id} with {current_user['email']}")
            except Exception as e:
                logger.warning(f"Auto-share failed: {e}")

        return JSONResponse({
            "success": True,
            "response": final_response,
            "presentation_url": presentation_url,
            "presentation_id": presentation_id,
        })

    except asyncio.TimeoutError:
        logger.error(f"/generate timed out after 180s for user={user_id}")
        return JSONResponse(
            {"success": False, "error": "Generation timed out. Please try a simpler request."},
            status_code=504
        )
    except Exception as e:
        logger.error(f"Text generation error: {e}\n{traceback.format_exc()}")
        return JSONResponse(
            {"success": False, "error": str(e)},
            status_code=500
        )


# ============================================================================
# WEBSOCKET VOICE STREAMING (Bidi-streaming via ADK)
# ============================================================================


@app.websocket("/ws")
async def websocket_voice(ws: WebSocket):
    """WebSocket endpoint for real-time voice interaction.

    Query params:
    - presentation_id: if provided, enters editing mode with edit_agent

    Protocol:
    - Client sends: {"type": "audio", "data": "<base64 PCM 16kHz 16-bit mono>"}
    - Client sends: {"type": "text", "data": "typed message"}
    - Server sends: {"type": "audio", "data": "<base64 PCM 24kHz 16-bit mono>"}
    - Server sends: {"type": "transcript", "role": "user"|"agent", "text": "..."}
    - Server sends: {"type": "status", "message": "Creating presentation..."}
    - Server sends: {"type": "url", "url": "https://docs.google.com/..."}
    - Server sends: {"type": "error", "message": "..."}
    """
    await ws.accept()

    user_id = f"user_{uuid.uuid4().hex[:8]}"
    session_id = f"session_{uuid.uuid4().hex[:8]}"

    # Check if this is an editing session
    presentation_id = ws.query_params.get("presentation_id")
    is_edit_mode = bool(presentation_id)
    active_runner = edit_runner if is_edit_mode else runner

    logger.info(f"WebSocket connected: {user_id}/{session_id} edit_mode={is_edit_mode} pres={presentation_id}")

    try:
        # Create session
        session = await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
        )

        # Import LiveRequestQueue for bidi-streaming
        from google.adk.agents.live_request_queue import LiveRequestQueue

        live_queue = LiveRequestQueue()

        # If editing, inject presentation state as initial context
        if is_edit_mode and presentation_id:
            try:
                from . import slidemakr as sm
                state = sm.get_presentation_state(presentation_id)
                context_msg = f"You are editing presentation '{state.get('title', '')}' (ID: {presentation_id}). "
                context_msg += f"It has {state.get('slide_count', 0)} slides. "
                context_msg += f"Here is the current state:\n{json.dumps(state, indent=2)[:8000]}"
                live_queue.send_content(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=context_msg)]
                    )
                )
                logger.info(f"Injected presentation state for editing: {state.get('title', '')}")
            except Exception as e:
                logger.error(f"Failed to load presentation state: {e}")
                await ws.send_json({"type": "status", "message": f"Warning: couldn't load presentation state"})

        # Configure for audio streaming using ADK RunConfig
        run_config = RunConfig(
            response_modalities=["AUDIO"],  # Pydantic warning is cosmetic, string works
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Aoede"
                    )
                )
            ),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
        )

        # Start the live agent run (edit_runner or voice runner)
        live_events = active_runner.run_live(
            session=session,
            live_request_queue=live_queue,
            run_config=run_config,
        )

        async def receive_from_client():
            """Read from WebSocket and push to LiveRequestQueue."""
            try:
                while True:
                    raw = await ws.receive_text()
                    msg = json.loads(raw)

                    if msg["type"] == "audio":
                        # Decode base64 PCM audio
                        audio_bytes = base64.b64decode(msg["data"])
                        live_queue.send_realtime(
                            types.Blob(data=audio_bytes, mime_type="audio/pcm")
                        )

                    elif msg["type"] == "text":
                        # Text message (typed input)
                        live_queue.send_content(
                            types.Content(
                                role="user",
                                parts=[types.Part.from_text(text=msg["data"])]
                            )
                        )

                    elif msg["type"] == "end":
                        # Client ending session
                        live_queue.close()
                        break

            except WebSocketDisconnect:
                live_queue.close()
            except Exception as e:
                logger.error(f"Receive error: {e}")
                live_queue.close()

        async def send_to_client():
            """Read from live agent events and push to WebSocket."""
            event_count = 0
            try:
                logger.info("send_to_client: starting event loop")
                async for event in live_events:
                    event_count += 1
                    # Debug: log every event
                    has_content = event.content is not None
                    has_parts = has_content and event.content.parts is not None
                    part_count = len(event.content.parts) if has_parts else 0
                    part_types = []
                    if has_parts:
                        for p in event.content.parts:
                            if p.inline_data:
                                part_types.append("audio")
                            elif p.text:
                                part_types.append(f"text({p.text[:50]})")
                            elif p.function_call:
                                part_types.append(f"fn_call({p.function_call.name})")
                            elif p.function_response:
                                part_types.append("fn_response")
                            else:
                                part_types.append("other")
                    logger.info(f"Event #{event_count}: content={has_content}, parts={part_count}, types={part_types}")

                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            # Audio output
                            if part.inline_data and part.inline_data.mime_type and "audio" in part.inline_data.mime_type:
                                audio_b64 = base64.b64encode(
                                    part.inline_data.data
                                ).decode("utf-8")
                                await ws.send_json({
                                    "type": "audio",
                                    "data": audio_b64
                                })

                            # Text output (transcript or response)
                            elif part.text:
                                role = event.content.role or "agent"
                                await ws.send_json({
                                    "type": "transcript",
                                    "role": role,
                                    "text": part.text
                                })

                                # Log agent transcript
                                if role == "agent":
                                    db.log_audio_interaction(
                                        user_id=user_id,
                                        session_id=session_id,
                                        transcript_agent=part.text,
                                    )

                            # Tool calls (agent is about to call a tool)
                            elif part.function_call:
                                tool_name = part.function_call.name
                                status_map = {
                                    'create_new_presentation': 'Creating presentation...',
                                    'execute_slide_requests': 'Building slides...',
                                    'get_presentation_state': 'Reading presentation...',
                                    'share_presentation_with_user': 'Sharing presentation...',
                                    'search_company_branding': 'Searching for brand info...',
                                }
                                status_msg = status_map.get(tool_name, f'Running {tool_name}...')
                                logger.info(f"Agent calling tool: {tool_name}")
                                await ws.send_json({
                                    "type": "status",
                                    "message": status_msg
                                })

                            # Tool responses (check for URLs, status)
                            elif part.function_response:
                                resp = part.function_response.response
                                if isinstance(resp, dict):
                                    logger.info(f"Tool response keys: {list(resp.keys())}")

                                    # Only send URL after slides are built (execute_slide_requests
                                    # returns success_count; create_new_presentation does not)
                                    if 'url' in resp and 'success_count' in resp:
                                        await ws.send_json({
                                            "type": "url",
                                            "url": resp['url']
                                        })
                                        logger.info(f"Slides done: {resp.get('success_count')}/{resp.get('total')}")
                                    elif 'url' in resp and 'presentation_id' in resp:
                                        # Presentation created but slides not built yet
                                        await ws.send_json({
                                            "type": "status",
                                            "message": "Presentation created, building slides..."
                                        })

                                    if 'status' in resp:
                                        await ws.send_json({
                                            "type": "status",
                                            "message": f"Tool result: {resp['status']}"
                                        })
                                    if 'error' in resp:
                                        logger.error(f"Tool error: {resp['error']}")
                                        await ws.send_json({
                                            "type": "status",
                                            "message": f"Error: {resp['error']}"
                                        })

                    # Check for input/output transcription events (ADK Live API)
                    if event.input_transcription and event.input_transcription.text:
                        logger.info(f"User transcription: {event.input_transcription.text} (finished={event.input_transcription.finished})")
                        await ws.send_json({
                            "type": "transcript",
                            "role": "user",
                            "text": event.input_transcription.text
                        })
                        if event.input_transcription.finished:
                            db.log_audio_interaction(
                                user_id=user_id,
                                session_id=session_id,
                                transcript_user=event.input_transcription.text,
                            )

                    if event.output_transcription and event.output_transcription.text:
                        logger.info(f"Agent transcription: {event.output_transcription.text}")
                        await ws.send_json({
                            "type": "transcript",
                            "role": "agent",
                            "text": event.output_transcription.text
                        })

                logger.info(f"send_to_client: event loop ended after {event_count} events")
            except WebSocketDisconnect:
                logger.info(f"send_to_client: WebSocket disconnected after {event_count} events")
            except Exception as e:
                logger.error(f"Send error after {event_count} events: {e}\n{traceback.format_exc()}")
                try:
                    await ws.send_json({
                        "type": "error",
                        "message": str(e)
                    })
                except Exception:
                    pass

        # Run both tasks concurrently (this is how ADK handles interruption)
        await asyncio.gather(
            receive_from_client(),
            send_to_client(),
        )

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {user_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}\n{traceback.format_exc()}")
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        logger.info(f"WebSocket session ended: {user_id}/{session_id}")


# ============================================================================
# SHARE ENDPOINT (for frontend compatibility)
# ============================================================================


@app.post("/share")
async def share_presentation(request: dict):
    """Share a presentation via email (REST endpoint for frontend)."""
    presentation_id = request.get("presentation_id")
    email = request.get("email")

    if not presentation_id or not email:
        return JSONResponse(
            {"success": False, "error": "presentation_id and email required"},
            status_code=400
        )

    try:
        from . import slidemakr as sm
        result = sm.share_presentation(presentation_id, email)

        if result.get('status') == 'shared':
            return JSONResponse({"success": True, **result})
        else:
            return JSONResponse({"success": False, **result}, status_code=500)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ============================================================================
# ERROR STATS (for debugging)
# ============================================================================


@app.get("/error-stats")
async def error_stats():
    """Get recent slide errors for debugging."""
    errors = db.get_error_stats()
    return JSONResponse({"errors": errors, "count": len(errors)})


# ============================================================================
# PRESENTATIONS API
# ============================================================================


@app.get("/api/presentations")
async def list_presentations(request: Request):
    """List presentations for the logged-in user."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"presentations": [], "logged_in": False})

    presentations = db.get_user_presentations(user["google_id"])
    return JSONResponse({
        "presentations": presentations,
        "logged_in": True,
    })


@app.post("/api/claim-presentation")
async def claim_presentation(request: Request):
    """Claim a presentation: share it with the logged-in user's Google account."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "error": "Not logged in"}, status_code=401)

    body = await request.json()
    presentation_id = body.get("presentation_id")
    if not presentation_id:
        return JSONResponse({"success": False, "error": "presentation_id required"}, status_code=400)

    try:
        from . import slidemakr as sm
        email = user.get("email")
        result = sm.share_presentation(presentation_id, email)

        # Also update the presentation record to associate with this user
        db.update_presentation_status(presentation_id, "shared", email=email)

        url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
        return JSONResponse({"success": True, "url": url, "email": email})
    except Exception as e:
        logging.error(f"Claim presentation error: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
