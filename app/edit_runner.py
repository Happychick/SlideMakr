"""
SlideMakr — text-driven edit runner (the STT→text voice-swap engine).

Applies a plain-text instruction to an existing deck via the narrow edit tools,
driven by gemini-2.5-flash (a text model that reliably reads tool responses —
unlike the native-audio model). This is the single implementation used by BOTH
the eval harness (app/edit_eval) and the product endpoints (/edit-audio,
/edit-text), so what we measure is exactly what we ship.

Credential-agnostic: it edits whatever presentation the caller points it at using
the credentials already set on the slidemakr context (service account for eval,
the user's OAuth for the product).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)


def build_text_edit_agent():
    from google.adk import Agent
    from .agent import edit_agent, EDIT_INSTRUCTION
    return Agent(
        model="gemini-2.5-flash",
        name="slidemakr_text_editor",
        description="Text-driven editor (STT→text voice pipeline + eval harness)",
        instruction=EDIT_INSTRUCTION,
        tools=edit_agent.tools,
    )


async def run_text_edit(presentation_id: str, instruction: str) -> Dict[str, Any]:
    """Apply a text instruction to a deck via the edit tools. Returns run stats."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from . import slidemakr

    svc = InMemorySessionService()
    runner = Runner(agent=build_text_edit_agent(), app_name="edit_runner", session_service=svc)
    session = await svc.create_session(app_name="edit_runner", user_id="edit_runner")

    state = slidemakr.get_presentation_state(presentation_id)
    ctx = (
        f"You are editing presentation '{state.get('title', '')}' (ID: {presentation_id}). "
        f"It has {state.get('slide_count', 0)} slides. Current state:\n"
        f"{json.dumps(state, indent=2)[:8000]}\n\n"
        f"User instruction: {instruction}\n"
        f"Apply it using the narrow tools, then call commit_edits('{presentation_id}')."
    )
    content = types.Content(role="user", parts=[types.Part.from_text(text=ctx)])

    data: Dict[str, Any] = {
        "total_requests": 0, "success_count": 0, "committed": False, "reply": "",
    }
    start = time.time()
    async for event in runner.run_async(
        user_id="edit_runner", session_id=session.id, new_message=content
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    data["reply"] += part.text
                if part.function_call and part.function_call.name == "commit_edits":
                    data["committed"] = True
                if part.function_response:
                    resp = part.function_response.response
                    if isinstance(resp, dict) and "success_count" in resp:
                        data["total_requests"] += resp.get("total", 0)
                        data["success_count"] += resp.get("success_count", 0)
    data["duration_seconds"] = round(time.time() - start, 2)
    return data
