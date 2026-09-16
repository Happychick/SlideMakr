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
import statistics
from datetime import datetime

from dotenv import load_dotenv

load_dotenv("app/.env")

from app.eval import run_full_eval, default_generate_fn  # noqa: E402
from app.edit_eval import run_edit_eval  # noqa: E402

RESULTS_DIR = "results"


def aggregate_runs(runs: list[dict], score_key: str, id_key: str) -> dict:
    """Aggregate N run dicts into per-case mean/min/max/stdev + overall mean.

    Tolerant of a run dict missing "results" (treated as no cases for that run).
    """
    per: dict[str, list[float]] = {}
    for r in runs:
        for case in r.get("results", []):
            per.setdefault(case[id_key], []).append(float(case.get(score_key) or 0.0))
    per_case = {
        cid: {
            "mean": statistics.fmean(v),
            "min": min(v),
            "max": max(v),
            "stdev": statistics.pstdev(v) if len(v) > 1 else 0.0,
        }
        for cid, v in per.items()
    }
    avg_overall = statistics.fmean(r["avg_overall"] for r in runs) if runs else 0.0
    return {"n": len(runs), "avg_overall": avg_overall, "per_case": per_case}


def _print_aggregate(kind: str, agg: dict, deck_links: list[str]) -> None:
    """Print the case | mean | min | max | stdev table plus every deck link."""
    print(f"\n=== {kind} aggregate over {agg['n']} runs: avg {agg['avg_overall']:.4f} ===")
    print(f"  {'case':22} {'mean':>8} {'min':>8} {'max':>8} {'stdev':>8}")
    for case_id, stats in agg["per_case"].items():
        print(
            f"  {case_id:22} {stats['mean']:8.4f} {stats['min']:8.4f} "
            f"{stats['max']:8.4f} {stats['stdev']:8.4f}"
        )
    if deck_links:
        print("  decks:")
        for link in deck_links:
            print(f"    {link}")


def _write(kind: str, payload: dict) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = os.path.join(RESULTS_DIR, kind)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{stamp}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    return path


def _write_aggregate(kind: str, agg: dict, n: int) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = os.path.join(RESULTS_DIR, kind)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{stamp}_aggregate_n{n}.json")
    with open(path, "w") as f:
        json.dump(agg, f, indent=2, default=str)
    return path


def _url(pid: str) -> str:
    return f"https://docs.google.com/presentation/d/{pid}/edit" if pid else "(no deck)"


def _share_all(res: dict, email: str) -> None:
    """Share every deck the eval produced with `email` so results are viewable."""
    from app import slidemakr
    for r in res.get("results", []):
        pid = r.get("presentation_id")
        if not pid:
            continue
        try:
            slidemakr.share_presentation(pid, email)
        except Exception as e:  # noqa: BLE001 — sharing is best-effort
            print(f"  (share failed for {pid}: {e})")


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
    ap.add_argument("--repeat", type=int, default=1,
                    help="run each selected eval N times and print an aggregate")
    args = ap.parse_args()

    run_creation = args.creation or args.both
    run_edit = args.edit or args.both
    if not (args.creation or args.edit or args.both):  # no flags → both
        run_creation = run_edit = True

    # Optional: auto-share every generated deck so results are viewable.
    share_email = os.getenv("EVAL_SHARE_EMAIL", "").strip()

    if run_creation:
        creation_runs = []
        creation_links = []
        for _ in range(args.repeat):
            res = await run_full_eval(default_generate_fn)
            path = _write("creation", res)
            if share_email:
                _share_all(res, share_email)
            _print_summary("creation", res, "avg_overall_score", "prompt_id", "overall_score")
            print(f"  → {path}")
            creation_runs.append(res)
            creation_links.extend(_url(r.get("presentation_id", "")) for r in res.get("results", []))
        if args.repeat > 1:
            agg = aggregate_runs(creation_runs, score_key="overall_score", id_key="prompt_id")
            agg_path = _write_aggregate("creation", agg, args.repeat)
            _print_aggregate("creation", agg, creation_links)
            print(f"  → {agg_path}")

    if run_edit:
        variants = tuple(v.strip() for v in args.edit_variants.split(",") if v.strip())
        edit_runs = []
        edit_links = []
        for _ in range(args.repeat):
            res = await run_edit_eval(variants=variants)
            path = _write("edit", res)
            if share_email:
                _share_all(res, share_email)
            _print_summary("edit", res, "avg_overall", "id", "overall")
            print(f"  → {path}")
            edit_runs.append(res)
            edit_links.extend(_url(r.get("presentation_id", "")) for r in res.get("results", []))
        if args.repeat > 1:
            agg = aggregate_runs(edit_runs, score_key="overall", id_key="id")
            agg_path = _write_aggregate("edit", agg, args.repeat)
            _print_aggregate("edit", agg, edit_links)
            print(f"  → {agg_path}")


if __name__ == "__main__":
    asyncio.run(_main())
