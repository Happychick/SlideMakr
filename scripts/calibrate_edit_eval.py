"""
Calibration view for the edit-eval: run each case, render the slide it was
scored on, and print instruction + transcript + scores + URL side by side so a
human can judge whether the score matches the actual result.

Also renders the first slide of each deck from the newest creation eval run
(if one exists in results/creation/), so scripts.calibration_report has
creation thumbnails to embed too.

Usage: python -m scripts.calibrate_edit_eval
"""

from __future__ import annotations

import asyncio
import glob
import json

from dotenv import load_dotenv

load_dotenv("app/.env")

import os  # noqa: E402

from app import slidemakr  # noqa: E402
from app.edit_eval import EDIT_CASES, _create_seed_deck, run_edit_case  # noqa: E402

# Which slide each case's verify function inspects.
SCORED_SLIDE = {"retitle": 0, "recolor_brand": 1, "vertical_flowchart": 2}
RENDER_DIR = os.path.join("results", "calibration")
CREATION_RESULTS_DIR = os.path.join("results", "creation")


def _render_creation_decks() -> None:
    """Render slide 1 of every deck in the newest creation eval run, if any."""
    all_json = sorted(glob.glob(os.path.join(CREATION_RESULTS_DIR, "*.json")))
    per_run_candidates = [p for p in all_json if "_aggregate" not in os.path.basename(p)]
    if not per_run_candidates:
        print("(no creation results found - skipping creation thumbnails)", flush=True)
        return
    latest = per_run_candidates[-1]
    with open(latest) as f:
        run = json.load(f)

    os.makedirs(RENDER_DIR, exist_ok=True)
    for case in run.get("results", []):
        prompt_id = case.get("prompt_id")
        pid = case.get("presentation_id")
        if not prompt_id or not pid:
            continue
        try:
            state = slidemakr.get_presentation_state(pid)
            slide_id = state["slides"][0]["slide_id"]
            png = slidemakr.get_slide_thumbnail(pid, slide_id, "MEDIUM")
        except Exception as e:  # noqa: BLE001 - render is best-effort
            print(f"  (creation render failed for {prompt_id}: {e})", flush=True)
            continue
        if not png:
            print(f"  (creation render failed for {prompt_id})", flush=True)
            continue
        path = os.path.join(RENDER_DIR, f"creation_{prompt_id}.png")
        with open(path, "wb") as f:
            f.write(png)
        print(f"  creation thumbnail: {path}", flush=True)


async def main() -> None:
    _render_creation_decks()
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
