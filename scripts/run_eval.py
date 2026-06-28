"""
Standalone eval runner — runs the full fast+accurate eval without the uvicorn
HTTP server (whose --reload restarts mid-run and kills long evals).

Usage:
    python -m scripts.run_eval
Prints the aggregated JSON result to stdout.
"""

from __future__ import annotations

import asyncio
import json
import time

from dotenv import load_dotenv

load_dotenv("app/.env")

from google.genai import types  # noqa: E402

from app import server  # noqa: E402  — reuse the configured runner + session service
from app.eval import run_full_eval  # noqa: E402


async def generate_fn(text: str) -> dict:
    session = await server.session_service.create_session(
        app_name=server.APP_NAME, user_id="eval_runner"
    )
    data = {
        "presentation_id": None,
        "duration_seconds": 0,
        "total_requests": 0,
        "success_count": 0,
    }
    start = time.time()
    content = types.Content(role="user", parts=[types.Part.from_text(text=text)])
    async for event in server.text_runner.run_async(
        user_id="eval_runner", session_id=session.id, new_message=content
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.function_response:
                    resp = part.function_response.response
                    if isinstance(resp, dict):
                        if "presentation_id" in resp:
                            data["presentation_id"] = resp["presentation_id"]
                        if "success_count" in resp:
                            data["total_requests"] += resp.get("total", 0)
                            data["success_count"] += resp.get("success_count", 0)
    data["duration_seconds"] = round(time.time() - start, 2)
    return data


def main() -> None:
    result = asyncio.run(run_full_eval(generate_fn))
    print("AVG_OVERALL:", result.get("avg_overall_score"))
    print("AVG_SCORES:", json.dumps(result.get("avg_scores", {})))
    for r in result.get("results", []):
        sc = r.get("scores", {})
        print(
            f"  {r['prompt_id']:16} overall={r.get('overall_score')} "
            f"adher={sc.get('instruction_adherence')} visual={sc.get('visual_quality')} "
            f"content={sc.get('content_richness')} dur={r.get('duration_seconds')}s"
        )


if __name__ == "__main__":
    main()
