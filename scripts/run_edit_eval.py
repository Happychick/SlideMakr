"""
Standalone voice → text → edit eval runner (decoupled from uvicorn).

Usage:
    python -m scripts.run_edit_eval [clean|noise|interrupt ...]
Defaults to the clean variant. Prints per-case scores + averages.
"""

from __future__ import annotations

import asyncio
import json
import sys

from dotenv import load_dotenv

load_dotenv("app/.env")

from app.edit_eval import run_edit_eval  # noqa: E402


def main() -> None:
    variants = tuple(sys.argv[1:]) or ("clean",)
    result = asyncio.run(run_edit_eval(variants=variants))
    if result.get("error"):
        print("ERROR:", result["error"])
        return
    print("SEED:", result["seed_presentation_id"])
    print("AVG_OVERALL:", result["avg_overall"])
    for r in result["results"]:
        if "error" in r:
            print(f"  {r['id']:20} {r['variant']:9} ERROR: {r['error'][:80]}")
            continue
        s = r["scores"]
        print(
            f"  {r['id']:20} {r['variant']:9} overall={r['overall']} "
            f"edit={s['edit_correct']} stt_acc={s['transcription']} "
            f"layout={s['layout']} speed={s['speed']} err={s['error_rate']} "
            f"| {r['total_seconds']}s (stt {r['stt_seconds']}s) committed={r['committed']}"
        )
        print(f"        transcript: \"{r['transcript'][:90]}\"")


if __name__ == "__main__":
    main()
