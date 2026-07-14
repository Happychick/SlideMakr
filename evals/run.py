"""
SlideMakr — the ONE eval runner.

Runs the fast+accurate evaluation and writes a timestamped JSON report to
`results/<creation|edit>/`. This is the single entry point; the `/admin/run-eval`
HTTP endpoint reuses the same underlying `app.eval` / `app.edit_eval` code.

Usage:
    python -m evals.run                      # both (default)
    python -m evals.run --creation           # creation only
    python -m evals.run --edit               # edit only
    python -m evals.run --edit --edit-variants clean,noise,interrupt

Decoupled from uvicorn (the dev server's --reload would kill long runs).
See evals/README.md for what the evaluations are.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv("app/.env")

from app.eval import run_full_eval, default_generate_fn  # noqa: E402
from app.edit_eval import run_edit_eval  # noqa: E402

RESULTS_DIR = "results"


def _write(kind: str, payload: dict) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = os.path.join(RESULTS_DIR, kind)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{stamp}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    return path


def _url(pid: str) -> str:
    return f"https://docs.google.com/presentation/d/{pid}/edit" if pid else "(no deck)"


def _print_summary(kind: str, res: dict, avg_key: str, id_key: str, score_key: str) -> None:
    """Print avg + per-case score AND the deck link for each result."""
    print(f"\n=== {kind} eval: avg {res.get(avg_key)} ===")
    for r in res.get("results", []):
        pid = r.get("presentation_id", "")
        label = r.get(id_key, "?")
        if "variant" in r:
            label = f"{label}/{r['variant']}"
        print(f"  {label:22} {score_key}={r.get(score_key)}  {_url(pid)}")


async def _main() -> None:
    ap = argparse.ArgumentParser(description="Run SlideMakr evals → results/")
    ap.add_argument("--creation", action="store_true", help="run the creation eval")
    ap.add_argument("--edit", action="store_true", help="run the edit eval")
    ap.add_argument("--both", action="store_true", help="run both (default)")
    ap.add_argument("--edit-variants", default="clean",
                    help="comma-separated: clean,noise,interrupt")
    args = ap.parse_args()

    run_creation = args.creation or args.both
    run_edit = args.edit or args.both
    if not (args.creation or args.edit or args.both):  # no flags → both
        run_creation = run_edit = True

    if run_creation:
        res = await run_full_eval(default_generate_fn)
        path = _write("creation", res)
        _print_summary("creation", res, "avg_overall_score", "prompt_id", "overall_score")
        print(f"  → {path}")

    if run_edit:
        variants = tuple(v.strip() for v in args.edit_variants.split(",") if v.strip())
        res = await run_edit_eval(variants=variants)
        path = _write("edit", res)
        _print_summary("edit", res, "avg_overall", "id", "overall")
        print(f"  → {path}")


if __name__ == "__main__":
    asyncio.run(_main())
