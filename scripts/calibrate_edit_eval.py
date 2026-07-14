"""
Calibration view for the edit-eval: run each case, render the slide it was
scored on, and print instruction + transcript + scores + URL side by side so a
human can judge whether the score matches the actual result.

Usage: python -m scripts.calibrate_edit_eval
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

load_dotenv("app/.env")

import os  # noqa: E402

from app import slidemakr  # noqa: E402
from app.edit_eval import EDIT_CASES, _create_seed_deck, run_edit_case  # noqa: E402

# Which slide each case's verify function inspects.
SCORED_SLIDE = {"retitle": 0, "recolor_brand": 1, "vertical_flowchart": 2}
RENDER_DIR = os.path.join("results", "calibration")


async def main() -> None:
    seed = await _create_seed_deck()
    print("SEED:", seed, flush=True)
    for case in EDIT_CASES:
        r = await run_edit_case(case, seed, "clean")
        pid = r["presentation_id"]
        idx = SCORED_SLIDE[case["id"]]
        state = slidemakr.get_presentation_state(pid)
        slide_id = state["slides"][idx]["slide_id"]
        png = slidemakr.get_slide_thumbnail(pid, slide_id, "MEDIUM")
        os.makedirs(RENDER_DIR, exist_ok=True)
        path = os.path.join(RENDER_DIR, f"edit_{case['id']}.png")
        if png:
            with open(path, "wb") as f:
                f.write(png)
        print("=" * 70, flush=True)
        print("CASE:", case["id"], "(scored on slide", idx + 1, ")", flush=True)
        print("  instruction:", case["instruction"], flush=True)
        print("  transcript :", r["transcript"], flush=True)
        print("  scores     :", r["scores"], "=> overall", r["overall"], flush=True)
        print("  url        : https://docs.google.com/presentation/d/%s/edit" % pid, flush=True)
        print("  rendered   :", path if png else "(render failed)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
