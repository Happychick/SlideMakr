"""
SlideMakr - FastAPI WebSocket Server

Serves the voice UI and handles real-time audio streaming via WebSocket.
Uses Google ADK's bidi-streaming for voice interaction with Gemini.

Endpoints:
- GET /           → serves frontend (index.html)
- GET /static/... → serves static files (JS, CSS)
- WS  /ws         → WebSocket for voice streaming
- POST /generate  → text-based slide generation
- POST /generate-audio → audio upload → Gemini transcription → generation (mobile fallback)
"""

import asyncio
import base64
import json
import logging
import os
import secrets
import time
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
from . import stripe_billing
from .instruction_contract import (
    build_instruction_contract,
    build_contract_prompt,
    score_instruction_adherence,
)

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

# Voice runner — currently unused for creation (SpeechRecognition + /generate is used instead)
# Kept for potential future voice-only flows
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


import time as time_module


def _prepare_generation_prompt(text: str) -> dict:
    """Prepare a low-talk, instruction-contract prompt for generation."""
    contract = build_instruction_contract(text)
    return {
        "mode": "silent_contract",
        "original_text": text,
        "contract": contract,
        "agent_prompt": build_contract_prompt(text, contract),
    }


def _billing_user_id(current_user: dict = None, provided_user_id: str = "") -> str:
    """Resolve billing identity for logged-in and guest users."""
    if current_user:
        return current_user["google_id"]
    return provided_user_id or f"guest_{uuid.uuid4().hex[:12]}"


def _checkout_required_response(credit_result: dict) -> JSONResponse:
    return JSONResponse({
        "success": False,
        "checkout_required": True,
        "error": "You have used your free deck. Buy credits to keep creating slides.",
        "credits": credit_result.get("credits", 0),
    }, status_code=402)


async def _run_generation(text: str, user_id: str, current_user: dict = None) -> dict:
    """Shared generation logic used by /generate and /generate-audio.

    Runs the text_agent, tracks metrics, auto-shares, and returns result dict.
    """
    generation_start = time_module.time()
    prepared = _prepare_generation_prompt(text)
    agent_prompt = prepared["agent_prompt"]
    instruction_contract = prepared["contract"]

    try:
        session = await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
        )

        final_response = ""
        presentation_url = None
        presentation_id = None

        tool_timings = {}
        slide_count = 0
        total_requests = 0
        total_success = 0
        total_errors_count = 0
        all_errors = []
        _current_tool_start = {}

        content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=agent_prompt)]
        )

        async def run_agent():
            nonlocal final_response, presentation_url, presentation_id
            nonlocal slide_count, total_requests, total_success, total_errors_count, all_errors
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
                            tool_name = part.function_call.name
                            logger.info(f"/generate event #{event_count}: tool_call={tool_name}")
                            _current_tool_start[tool_name] = time_module.time()
                        if part.function_response:
                            resp_data = part.function_response.response
                            fn_name = part.function_response.name if hasattr(part.function_response, 'name') else None
                            if fn_name and fn_name in _current_tool_start:
                                elapsed = time_module.time() - _current_tool_start.pop(fn_name)
                                tool_timings[fn_name] = tool_timings.get(fn_name, 0) + round(elapsed, 2)

                            if isinstance(resp_data, dict):
                                logger.info(f"/generate event #{event_count}: tool_response keys={list(resp_data.keys())}")
                                if 'url' in resp_data:
                                    presentation_url = resp_data['url']
                                if 'presentation_id' in resp_data:
                                    presentation_id = resp_data['presentation_id']
                                if 'success_count' in resp_data:
                                    total_requests += resp_data.get('total', 0)
                                    total_success += resp_data.get('success_count', 0)
                                    total_errors_count += resp_data.get('error_count', 0)
                                    if resp_data.get('errors'):
                                        all_errors.extend(resp_data['errors'])
                                if 'slide_count' in resp_data:
                                    slide_count = resp_data['slide_count']
            logger.info(f"/generate complete: {event_count} events, url={presentation_url}")

        await asyncio.wait_for(run_agent(), timeout=300)

        duration = round(time_module.time() - generation_start, 2)

        if current_user and presentation_id:
            try:
                from . import slidemakr as sm
                sm.share_presentation(presentation_id, current_user["email"])
                logger.info(f"Auto-shared {presentation_id} with {current_user['email']}")
            except Exception as e:
                logger.warning(f"Auto-share failed: {e}")

        if presentation_id:
            try:
                adherence_result = None
                try:
                    from . import slidemakr as sm
                    state = sm.get_presentation_state(presentation_id)
                    adherence_result = score_instruction_adherence(
                        instruction_contract,
                        state,
                    )
                except Exception as e:
                    logger.warning(f"Failed to score instruction adherence: {e}")

                db.save_presentation_metrics(
                    presentation_id=presentation_id,
                    user_id=user_id,
                    instructions=text,
                    slide_count=slide_count,
                    request_count=total_requests,
                    success_count=total_success,
                    error_count=total_errors_count,
                    duration_seconds=duration,
                    tool_timings=tool_timings,
                    errors=all_errors,
                    instruction_contract=instruction_contract,
                    adherence_result=adherence_result,
                )
            except Exception as e:
                logger.warning(f"Failed to save metrics: {e}")

        logger.info(f"/generate metrics: {duration}s, {total_requests} requests, "
                    f"{total_errors_count} errors, {slide_count} slides")

        return {
            "success": True,
            "response": final_response,
            "presentation_url": presentation_url,
            "presentation_id": presentation_id,
            "duration_seconds": duration,
            "generation_mode": prepared["mode"],
            "instruction_contract": instruction_contract,
            "instruction_adherence": adherence_result if presentation_id else None,
        }

    except asyncio.TimeoutError:
        logger.error(f"/generate timed out after 300s for user={user_id}")
        return {"success": False, "error": "Generation timed out. Please try a simpler request."}
    except Exception as e:
        logger.error(f"Text generation error: {e}\n{traceback.format_exc()}")
        return {"success": False, "error": str(e)}


@app.post("/generate")
async def generate_from_text(request: Request):
    """Generate a presentation from text instructions."""
    body = await request.json()
    text = body.get("text", "")
    if not text:
        return JSONResponse({"success": False, "error": "No text provided"}, status_code=400)

    current_user = get_current_user(request)
    if current_user:
        user_id = current_user["google_id"]
    else:
        user_id = _billing_user_id(None, body.get("user_id", ""))

    credit_result = db.consume_generation_credit(user_id)
    if not credit_result.get("allowed"):
        return _checkout_required_response(credit_result)

    logger.info(f"/generate called: user={user_id}, text={text[:100]}...")

    # Clean up voice transcripts (raw speech → clear instructions)
    is_voice = body.get("is_voice", False)
    if is_voice and len(text) > 50:
        try:
            from google import genai
            client = genai.Client()
            cleanup = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"""Extract the presentation instructions from this voice transcript.
Remove filler words, false starts, and conversational fluff.
Return ONLY the clean, clear instructions for what presentation to make.

Voice transcript: "{text}"

Clean instructions:""",
            )
            cleaned = cleanup.text.strip()
            if cleaned:
                logger.info(f"/generate cleaned voice: {cleaned[:100]}...")
                text = cleaned
        except Exception as e:
            logger.warning(f"Voice cleanup failed, using raw transcript: {e}")

    result = await _run_generation(text, user_id, current_user)
    status_code = 200 if result.get("success") else 500
    return JSONResponse(result, status_code=status_code)


@app.post("/generate-audio")
async def generate_from_audio(request: Request):
    """Generate a presentation from an audio recording.

    Accepts multipart form data with an audio file, transcribes it via Gemini,
    then runs the same generation pipeline as /generate.
    Used as fallback for mobile browsers without SpeechRecognition API.
    """
    from fastapi import UploadFile
    form = await request.form()
    audio_file = form.get("audio")

    if not audio_file:
        return JSONResponse({"success": False, "error": "No audio file provided"}, status_code=400)

    audio_bytes = await audio_file.read()
    mime_type = audio_file.content_type or "audio/webm"

    if len(audio_bytes) > 10 * 1024 * 1024:
        return JSONResponse({"success": False, "error": "Audio file too large (max 10MB)"}, status_code=400)

    if len(audio_bytes) < 1000:
        return JSONResponse({"success": False, "error": "Audio recording too short. Please try again."}, status_code=400)

    logger.info(f"/generate-audio: {len(audio_bytes)} bytes, mime={mime_type}")

    # Transcribe audio via Gemini
    try:
        from google import genai
        from google.genai import types as genai_types
        client = genai.Client()
        transcription = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                        genai_types.Part.from_text(
                            text="Transcribe this audio recording. The user is giving instructions "
                                 "for creating a presentation. Return ONLY the transcribed text, "
                                 "cleaned up to remove filler words and false starts. "
                                 "Return clear, actionable instructions."
                        ),
                    ],
                )
            ],
        )
        text = transcription.text.strip()
    except Exception as e:
        logger.error(f"/generate-audio transcription failed: {e}")
        return JSONResponse({"success": False, "error": f"Could not transcribe audio: {e}"}, status_code=500)

    if not text:
        return JSONResponse({"success": False, "error": "Could not understand the audio. Please try again."}, status_code=400)

    logger.info(f"/generate-audio transcribed: {text[:100]}...")

    current_user = get_current_user(request)
    user_id = _billing_user_id(current_user, form.get("user_id", ""))

    credit_result = db.consume_generation_credit(user_id)
    if not credit_result.get("allowed"):
        return _checkout_required_response(credit_result)

    result = await _run_generation(text, user_id, current_user)
    result["transcript"] = text  # Send transcript back to frontend
    status_code = 200 if result.get("success") else 500
    return JSONResponse(result, status_code=status_code)


# ============================================================================
# BILLING — Stripe Checkout credit packs
# ============================================================================

@app.get("/billing/credits")
async def billing_credits(request: Request):
    """Return remaining credits for the current user or guest user_id."""
    current_user = get_current_user(request)
    user_id = _billing_user_id(current_user, request.query_params.get("user_id", ""))
    return JSONResponse({"user_id": user_id, **db.get_user_credits(user_id)})


@app.post("/billing/checkout")
async def billing_checkout(request: Request):
    """Create a hosted Stripe Checkout Session for a credit pack."""
    body = await request.json()
    current_user = get_current_user(request)
    user_id = _billing_user_id(current_user, body.get("user_id", ""))
    package_id = body.get("package_id", "credits_10")
    origin = str(request.base_url).rstrip("/")
    success_url = body.get("success_url") or f"{origin}/?checkout=success"
    cancel_url = body.get("cancel_url") or f"{origin}/?checkout=cancel"

    try:
        session = stripe_billing.create_checkout_session(
            user_id=user_id,
            package_id=package_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return JSONResponse(session)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"Stripe checkout failed: {e}")
        return JSONResponse({"error": "Checkout is not available right now."}, status_code=500)


@app.post("/billing/webhook")
async def billing_webhook(request: Request):
    """Receive Stripe webhooks and credit users after checkout completion."""
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe_billing.verify_webhook_event(payload, signature)
    except Exception as e:
        logger.warning(f"Stripe webhook verification failed: {e}")
        return JSONResponse({"error": "invalid_signature"}, status_code=400)

    if event.get("type") == "checkout.session.completed":
        result = stripe_billing.handle_checkout_completed(event["data"]["object"])
        return JSONResponse(result)
    return JSONResponse({"status": "ignored", "type": event.get("type", "")})


# ============================================================================
# WEBSOCKET AUTH TOKENS
# ============================================================================

_ws_tokens: dict = {}  # token -> {google_id, expires}
_addon_tokens: dict = {}  # token -> {google_id, expires}


@app.post("/api/ws-token")
async def create_ws_token(request: Request):
    """Create a short-lived token for authenticating WebSocket connections."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    token = secrets.token_urlsafe(32)
    _ws_tokens[token] = {
        "google_id": user["google_id"],
        "expires": time.time() + 300,  # 5 minutes
    }
    # Clean up expired tokens
    now = time.time()
    expired = [k for k, v in _ws_tokens.items() if v["expires"] < now]
    for k in expired:
        del _ws_tokens[k]
    return JSONResponse({"token": token})


@app.post("/api/addon-token")
async def create_addon_token(request: Request):
    """Create a short-lived token for Google Slides add-on calls."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    token = secrets.token_urlsafe(32)
    _addon_tokens[token] = {
        "google_id": user["google_id"],
        "expires": time.time() + 900,
    }
    return JSONResponse({"token": token, "expires_in": 900})


def _resolve_addon_user(request: Request) -> str:
    user = get_current_user(request)
    if user:
        return user["google_id"]
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        token_data = _addon_tokens.get(token)
        if token_data and token_data["expires"] > time.time():
            return token_data["google_id"]
    return ""


async def _run_addon_edit(presentation_id: str, text: str, user_id: str) -> dict:
    """Run a silent text edit against an active Google Slides presentation."""
    session = await session_service.create_session(app_name=APP_NAME, user_id=user_id)
    from . import slidemakr as sm

    state = sm.get_presentation_state(presentation_id)
    prompt = (
        f"You are editing presentation ID {presentation_id}. "
        f"Current state:\n{json.dumps(state, indent=2)[:8000]}\n\n"
        "Apply this user request using narrow tools and commit_edits. "
        "Do not ask clarifying questions unless impossible to infer.\n\n"
        f"User request: {text}"
    )
    content = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    final_response = ""
    async for event in text_runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=content,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    final_response += part.text
    return {
        "success": True,
        "presentation_id": presentation_id,
        "response": final_response,
    }


@app.post("/api/addon/edit")
async def addon_edit(request: Request):
    """Apply a silent text edit from the Google Slides add-on sidebar."""
    user_id = _resolve_addon_user(request)
    if not user_id:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    body = await request.json()
    presentation_id = body.get("presentation_id", "")
    text = body.get("text", "")
    if not presentation_id or not text:
        return JSONResponse({"error": "presentation_id and text required"}, status_code=400)
    try:
        result = await _run_addon_edit(presentation_id, text, user_id)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Add-on edit failed: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


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
    ws_token = ws.query_params.get("token")
    is_drive_mode = ws.query_params.get("drive") == "1"

    # Authenticate user via ws-token (for Drive Picker flow)
    refresh_token = None
    if ws_token:
        token_data = _ws_tokens.pop(ws_token, None)
        if token_data and token_data["expires"] > time.time():
            google_id = token_data["google_id"]
            user_record = db.get_user(google_id)
            if user_record:
                refresh_token = user_record.get("refresh_token")
                user_id = f"user_{google_id[:8]}"

    # Set user credentials for this context if available
    from . import slidemakr as sm
    if refresh_token:
        sm.set_user_credentials(refresh_token)

    if not presentation_id and not is_drive_mode:
        await ws.send_json({"type": "error", "message": "presentation_id or drive=1 required"})
        await ws.close()
        return

    is_edit_mode = True  # All /ws connections are now editing sessions
    active_runner = edit_runner

    logger.info(f"WebSocket connected: {user_id}/{session_id} pres={presentation_id} drive_mode={is_drive_mode} has_user_creds={refresh_token is not None}")

    try:
        # Create session
        session = await session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
        )

        # Import LiveRequestQueue for bidi-streaming
        from google.adk.agents.live_request_queue import LiveRequestQueue

        live_queue = LiveRequestQueue()

        # Inject initial context based on mode
        if is_drive_mode and not presentation_id:
            # Drive mode: user will tell us which presentation to work with
            context_msg = (
                "You are in Drive mode. The user is connected to their Google Drive. "
                "They can ask you to find, open, duplicate, or edit any of their presentations. "
                "Use search_drive_presentations to find presentations, "
                "duplicate_presentation to copy one, "
                "and open_presentation to load one for editing. "
                "Wait for the user to tell you what they'd like to do."
            )
            live_queue.send_content(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=context_msg)]
                )
            )
            logger.info("Injected Drive mode context")
        elif presentation_id:
            try:
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

        # Context rot defense: track tool calls per session
        CONTEXT_REFRESH_INTERVAL = 5   # Re-inject state every N tool calls
        CONTEXT_WARN_THRESHOLD = 15    # Suggest new session after N tool calls
        tool_call_count = 0

        async def send_to_client():
            """Read from live agent events and push to WebSocket."""
            nonlocal tool_call_count, presentation_id
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
                                tool_call_count += 1
                                status_map = {
                                    'create_new_presentation': 'Creating presentation...',
                                    'execute_slide_requests': 'Building slides...',
                                    'get_presentation_state': 'Reading presentation...',
                                    'share_presentation_with_user': 'Sharing presentation...',
                                    'search_company_branding': 'Searching for brand info...',
                                }
                                status_msg = status_map.get(tool_name, f'Running {tool_name}...')
                                logger.info(f"Agent calling tool: {tool_name} (call #{tool_call_count})")
                                await ws.send_json({
                                    "type": "status",
                                    "message": status_msg
                                })

                                # Context rot: re-inject presentation state periodically
                                if (tool_call_count % CONTEXT_REFRESH_INTERVAL == 0
                                        and tool_call_count > 0
                                        and presentation_id
                                        and tool_name != 'get_presentation_state'):
                                    try:
                                        from . import slidemakr as sm
                                        state = sm.get_presentation_state(presentation_id)
                                        refresh_msg = (
                                            f"[Context refresh — current state of presentation "
                                            f"'{state.get('title', '')}' with {state.get('slide_count', 0)} slides]\n"
                                            f"{json.dumps(state, indent=2)[:4000]}"
                                        )
                                        live_queue.send_content(
                                            types.Content(
                                                role="user",
                                                parts=[types.Part.from_text(text=refresh_msg)]
                                            )
                                        )
                                        logger.info(f"Context refresh injected at tool call #{tool_call_count}")
                                    except Exception as e:
                                        logger.warning(f"Context refresh failed: {e}")

                                # Context rot: warn after many tool calls
                                if tool_call_count == CONTEXT_WARN_THRESHOLD:
                                    await ws.send_json({
                                        "type": "status",
                                        "message": "Tip: For best results, consider starting a fresh editing session."
                                    })
                                    logger.info(f"Context rot warning sent at {tool_call_count} tool calls")

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
                                        # Presentation opened/created — send URL + name to frontend
                                        presentation_id = resp['presentation_id']
                                        url_msg = {
                                            "type": "url",
                                            "url": resp['url']
                                        }
                                        # Include presentation name if available
                                        if 'name' in resp:
                                            url_msg['name'] = resp['name']
                                        elif resp.get('verification', {}).get('title'):
                                            url_msg['name'] = resp['verification']['title']
                                        await ws.send_json(url_msg)

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
        sm.clear_user_credentials()
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
# METRICS & ERROR STATS
# ============================================================================


@app.get("/metrics")
async def metrics_dashboard():
    """Quality dashboard — timing, error rates, and trends."""
    summary = db.get_metrics_summary()
    return JSONResponse(summary)


@app.get("/error-stats")
async def error_stats():
    """Get recent slide errors for debugging."""
    errors = db.get_error_stats()
    return JSONResponse({"errors": errors, "count": len(errors)})


@app.get("/error-patterns")
async def error_patterns():
    """Get error patterns grouped by message — shows which have auto-fixes."""
    patterns = db.get_error_patterns()
    unfixed = [p for p in patterns if not p['has_auto_fix']]
    return JSONResponse({
        "patterns": patterns,
        "total_patterns": len(patterns),
        "unfixed_patterns": len(unfixed),
        "top_unfixed": unfixed[:5],
    })


# ============================================================================
# EVAL PIPELINE
# ============================================================================


@app.post("/admin/run-eval")
async def run_eval():
    """Run the full eval suite — creates real presentations and scores them.

    WARNING: This creates real Google Slides presentations and takes ~2-3 minutes.
    """
    from .eval import run_full_eval

    async def generate_fn(text: str) -> dict:
        """Wrapper to call our generate logic for eval."""
        from google.adk.sessions import InMemorySessionService
        eval_session_service = InMemorySessionService()
        eval_session = await eval_session_service.create_session(
            app_name=APP_NAME, user_id="eval_runner"
        )

        result_data = {
            'presentation_id': None,
            'duration_seconds': 0,
            'total_requests': 0,
            'success_count': 0,
        }

        import time as time_module
        start = time_module.time()

        content = types.Content(
            role="user", parts=[types.Part.from_text(text=text)]
        )

        async for event in text_runner.run_async(
            user_id="eval_runner",
            session_id=eval_session.id,
            new_message=content,
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.function_response:
                        resp = part.function_response.response
                        if isinstance(resp, dict):
                            if 'presentation_id' in resp:
                                result_data['presentation_id'] = resp['presentation_id']
                            if 'success_count' in resp:
                                result_data['total_requests'] += resp.get('total', 0)
                                result_data['success_count'] += resp.get('success_count', 0)

        result_data['duration_seconds'] = round(time_module.time() - start, 2)
        return result_data

    try:
        eval_result = await asyncio.wait_for(run_full_eval(generate_fn), timeout=600)
        return JSONResponse(eval_result)
    except asyncio.TimeoutError:
        return JSONResponse(
            {"error": "Eval timed out after 10 minutes"}, status_code=504
        )
    except Exception as e:
        logger.error(f"Eval failed: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/admin/eval-history")
async def eval_history():
    """Get past eval run results."""
    from .eval import get_eval_history
    history = get_eval_history()
    return JSONResponse({"runs": history, "count": len(history)})


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


# ============================================================================
# DRIVE PICKER — browse & select existing presentations
# ============================================================================

@app.get("/api/drive/presentations")
async def drive_presentations(request: Request):
    """List the user's Google Slides presentations from their Drive."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    # Get stored refresh token
    user_record = db.get_user(user["google_id"])
    refresh_token = user_record.get("refresh_token") if user_record else None
    if not refresh_token:
        return JSONResponse({"error": "drive_auth_required"}, status_code=401)

    query = request.query_params.get("q", "")

    try:
        from . import slidemakr as sm
        files = sm.list_user_presentations(refresh_token, query=query)
        return JSONResponse({"presentations": files})
    except Exception as e:
        error_msg = str(e)
        logging.error(f"Drive list error: {error_msg}")
        if "invalid_grant" in error_msg.lower() or "token" in error_msg.lower():
            return JSONResponse({"error": "drive_auth_required"}, status_code=401)
        return JSONResponse({"error": error_msg}, status_code=500)


@app.post("/api/drive/select")
async def drive_select(request: Request):
    """Select a Drive presentation for editing — shares it with the service account."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    body = await request.json()
    presentation_id = body.get("presentation_id")
    if not presentation_id:
        return JSONResponse({"error": "presentation_id required"}, status_code=400)

    user_record = db.get_user(user["google_id"])
    refresh_token = user_record.get("refresh_token") if user_record else None
    if not refresh_token:
        return JSONResponse({"error": "drive_auth_required"}, status_code=401)

    try:
        from . import slidemakr as sm
        result = sm.share_with_service_account(refresh_token, presentation_id)
        return JSONResponse({"success": True, **result})
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    except Exception as e:
        error_msg = str(e)
        logging.error(f"Drive select error: {error_msg}")
        if "invalid_grant" in error_msg.lower() or "token" in error_msg.lower():
            return JSONResponse({"error": "drive_auth_required"}, status_code=401)
        return JSONResponse({"error": error_msg}, status_code=500)
