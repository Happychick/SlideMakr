"""
SlideMakr — the ONE eval runner.

Runs the fast+accurate evaluation and writes a timestamped JSON report to
`results/<creation|edit|edit_flow>/`. This is the single entry point; the
`/admin/run-eval` HTTP endpoint reuses the same underlying `app.eval` /
`app.edit_eval` code.

Usage:
    python -m evals.run                      # edit-flow (default): 1 deck,
                                              # 6 scripted edits, hard budget
    python -m evals.run --deck <id>          # reuse a deck, skip creation
    python -m evals.run --creation           # creation only (5+2 prompts)
    python -m evals.run --edit               # legacy per-case edit eval
    python -m evals.run --both                # creation + legacy edit eval
    python -m evals.run --edit --edit-variants clean,noise,interrupt

Cost control: before anything runs — before even the `.env`/client-creating
imports — the planned deck count and LLM turn count are computed and printed
as `PLAN: decks=<n> llm_turns=<n> budget=<N>`. If planned decks exceed
`--budget-decks` (default 1), the run exits(2) with nothing created. This
replaced a prior eval shape that could spin up 50+ decks with no cost control.

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

RESULTS_DIR = "results"

# Fixed formulas for the deck/turn budget plan (see module docstring). The
# legacy per-case edit eval always creates 1 seed deck + 3 case decks
# (retitle, recolor_brand, vertical_flowchart), independent of --edit-variants
# (variants replay the SAME case decks, they don't create new ones).
_EDIT_CASE_DECKS = 1 + 3


def _creation_prompt_count() -> int:
    """Number of decks one creation-eval run creates (5 standard + 2 first-shot)."""
    from app.eval import EVAL_PROMPTS, FIRST_SHOT_ADHERENCE_PROMPTS
    return len(EVAL_PROMPTS) + len(FIRST_SHOT_ADHERENCE_PROMPTS)


def _edit_flow_edit_count() -> int:
    from app.edit_flow_eval import EDIT_FLOW
    return len(EDIT_FLOW["edits"])


def select_kinds(args: argparse.Namespace) -> tuple[bool, bool, bool]:
    """Which eval kind(s) to run: (creation, edit, edit_flow).

    `--edit-flow` is the default when no kind flag is given at all. `--both`
    keeps its historical meaning (creation + legacy edit eval) and does NOT
    imply edit-flow.
    """
    run_creation = args.creation or args.both
    run_edit = args.edit or args.both
    run_edit_flow = args.edit_flow
    if not (args.creation or args.edit or args.both or args.edit_flow):
        run_edit_flow = True
    return run_creation, run_edit, run_edit_flow


def plan_budget(
    *, run_creation: bool, run_edit: bool, run_edit_flow: bool, repeat: int, has_deck: bool,
    vision: bool = True,
) -> dict:
    """Planned decks + LLM turns for the selected kind(s), BEFORE anything runs.

    decks: edit-flow = 1 (0 with --deck); creation = prompts × repeat;
    edit = (1 + 3) × repeat.
    llm_turns: only edit-flow drives this — 1 create turn (0 with --deck)
    plus one turn per scripted edit, plus (when `vision`) one more
    `review_slide_layout` call per edit, × repeat.
    """
    decks = 0
    llm_turns = 0
    if run_creation:
        decks += _creation_prompt_count() * repeat
    if run_edit:
        decks += _EDIT_CASE_DECKS * repeat
    if run_edit_flow:
        n_edits = _edit_flow_edit_count()
        decks += (0 if has_deck else 1) * repeat
        llm_turns += ((0 if has_deck else 1) + n_edits + (n_edits if vision else 0)) * repeat
    return {"decks": decks, "llm_turns": llm_turns}


def aggregate_runs(
    runs: list[dict], score_key: str, id_key: str, avg_key: str, items_key: str = "results",
) -> dict:
    """Aggregate N run dicts into per-case mean/min/max/stdev + overall mean.

    `avg_key` is the top-level average-score field name, which differs between
    the creation eval ("avg_overall_score") and the edit/edit-flow evals
    ("avg_overall"). `items_key` is the top-level field holding the per-case
    list — "results" for creation/edit, "edits" for edit-flow.

    Tolerant of a run dict missing `items_key` (treated as no cases for that
    run). A case score that is missing or None is excluded from that case's
    mean/min/max/stdev and counted in "missing"; a real 0.0 is kept.
    """
    per: dict[str, list[float]] = {}
    missing: dict[str, int] = {}
    for r in runs:
        for case in r.get(items_key, []):
            cid = case[id_key]
            score = case.get(score_key)
            if score is None:
                missing[cid] = missing.get(cid, 0) + 1
                per.setdefault(cid, [])
            else:
                per.setdefault(cid, []).append(float(score))
                missing.setdefault(cid, 0)
    per_case = {
        cid: {
            "mean": statistics.fmean(v) if v else None,
            "min": min(v) if v else None,
            "max": max(v) if v else None,
            "stdev": statistics.pstdev(v) if len(v) > 1 else 0.0,
            "missing": missing.get(cid, 0),
        }
        for cid, v in per.items()
    }
    avg_overall = statistics.fmean(r[avg_key] for r in runs) if runs else 0.0
    return {"n": len(runs), "avg_overall": avg_overall, "per_case": per_case}


def _print_aggregate(kind: str, agg: dict, deck_links: list[str]) -> None:
    """Print the case | mean | min | max | stdev | missing table plus every deck link."""
    print(f"\n=== {kind} aggregate over {agg['n']} runs: avg {agg['avg_overall']:.4f} ===")
    print(f"  {'case':22} {'mean':>8} {'min':>8} {'max':>8} {'stdev':>8} {'missing':>8}")
    for case_id, stats in agg["per_case"].items():
        def _fmt(v):
            return f"{v:8.4f}" if v is not None else f"{'—':>8}"
        print(
            f"  {case_id:22} {_fmt(stats['mean'])} {_fmt(stats['min'])} "
            f"{_fmt(stats['max'])} {stats['stdev']:8.4f} {stats['missing']:8d}"
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


def _print_edit_flow(res: dict) -> None:
    """Deck link once at the top, then # | edit | landed | usability | secs |
    overall, with each edit's reasons printed indented underneath, then
    avg_overall."""
    print(f"\n=== edit-flow: avg_overall {res.get('avg_overall')} ===")
    print(f"  deck: {res.get('deck_url')}")
    header = f"  {'#':>2}  {'edit':46} {'landed':>7} {'usability':>10} {'secs':>7} {'overall':>8}"
    print(header)
    for e in res.get("edits", []):
        instr = e.get("instruction", "")[:46]
        print(
            f"  {e['i']:>2}  {instr:46} {e['landed']:7.2f} {e['usability']:10.2f} "
            f"{e['seconds']:7.1f} {e['overall']:8.4f}"
        )
        for reason in e.get("reasons", []):
            print(f"        {reason}")
    print(f"  avg_overall: {res.get('avg_overall')}")


async def _main() -> None:
    ap = argparse.ArgumentParser(description="Run SlideMakr evals → results/")
    ap.add_argument("--creation", action="store_true", help="run the creation eval")
    ap.add_argument("--edit", action="store_true", help="run the legacy per-case edit eval")
    ap.add_argument("--both", action="store_true", help="run creation + legacy edit eval")
    ap.add_argument("--edit-flow", action="store_true",
                     help="run the one-deck edit-flow eval (default when no kind flag given)")
    ap.add_argument("--deck", default=None,
                     help="edit-flow only: reuse this deck id — skips creation")
    ap.add_argument("--no-vision", action="store_true",
                     help="edit-flow only: skip the review_slide_layout vision "
                          "part of usability (default: vision on)")
    ap.add_argument("--final-look", dest="final_look", action="store_true", default=None,
                     help="edit-flow only: force run_text_edit's final_look=True "
                          "(default: SLIDEMAKR_FINAL_LOOK env, off)")
    ap.add_argument("--no-final-look", dest="final_look", action="store_false",
                     help="edit-flow only: force run_text_edit's final_look=False")
    ap.add_argument("--budget-decks", type=int, default=1,
                     help="hard cap on decks this run may create (default 1)")
    ap.add_argument("--edit-variants", default="clean",
                    help="comma-separated: clean,noise,interrupt (legacy --edit only)")
    ap.add_argument("--repeat", type=int, default=1,
                    help="run each selected eval N times and print an aggregate")
    args = ap.parse_args()

    run_creation, run_edit, run_edit_flow = select_kinds(args)

    # Budget gate — computed and printed BEFORE any .env-dependent import that
    # could create a client, and before any network call.
    vision = not args.no_vision
    plan = plan_budget(
        run_creation=run_creation, run_edit=run_edit, run_edit_flow=run_edit_flow,
        repeat=args.repeat, has_deck=bool(args.deck), vision=vision,
    )
    print(f"PLAN: decks={plan['decks']} llm_turns={plan['llm_turns']} budget={args.budget_decks}")
    if plan["decks"] > args.budget_decks:
        print(
            f"Refusing to run: planned decks ({plan['decks']}) exceed "
            f"--budget-decks ({args.budget_decks}). Raise --budget-decks, or pass "
            "--deck <id> to reuse an existing deck instead of creating one."
        )
        raise SystemExit(2)

    from dotenv import load_dotenv
    load_dotenv("app/.env")

    # Optional: auto-share every generated deck so results are viewable.
    share_email = os.getenv("EVAL_SHARE_EMAIL", "").strip()

    if run_creation:
        from app.eval import run_full_eval, default_generate_fn
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
            agg = aggregate_runs(
                creation_runs, score_key="overall_score", id_key="prompt_id",
                avg_key="avg_overall_score",
            )
            agg_path = _write_aggregate("creation", agg, args.repeat)
            _print_aggregate("creation", agg, creation_links)
            print(f"  → {agg_path}")

    if run_edit:
        from app.edit_eval import run_edit_eval
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
            agg = aggregate_runs(
                edit_runs, score_key="overall", id_key="id", avg_key="avg_overall",
            )
            agg_path = _write_aggregate("edit", agg, args.repeat)
            _print_aggregate("edit", agg, edit_links)
            print(f"  → {agg_path}")

    if run_edit_flow:
        import functools
        from app.eval import default_generate_fn
        from app.edit_runner import run_text_edit, FINAL_LOOK as _ENV_FINAL_LOOK
        from app.edit_flow_eval import run_edit_flow as _run_edit_flow

        final_look = _ENV_FINAL_LOOK if args.final_look is None else args.final_look
        edit_fn = functools.partial(run_text_edit, final_look=final_look)

        edit_flow_runs = []
        for _ in range(args.repeat):
            res = await _run_edit_flow(
                default_generate_fn, edit_fn, deck_id=args.deck, vision=vision,
            )
            path = _write("edit_flow", res)
            if share_email and res.get("deck_id"):
                from app import slidemakr
                try:
                    slidemakr.share_presentation(res["deck_id"], share_email)
                except Exception as e:  # noqa: BLE001 — sharing is best-effort
                    print(f"  (share failed for {res['deck_id']}: {e})")
            _print_edit_flow(res)
            print(f"  → {path}")
            edit_flow_runs.append(res)
        if args.repeat > 1:
            agg = aggregate_runs(
                edit_flow_runs, score_key="overall", id_key="i", avg_key="avg_overall",
                items_key="edits",
            )
            agg_path = _write_aggregate("edit_flow", agg, args.repeat)
            deck_links = [r.get("deck_url") for r in edit_flow_runs if r.get("deck_url")]
            _print_aggregate("edit_flow", agg, deck_links)
            print(f"  → {agg_path}")


if __name__ == "__main__":
    asyncio.run(_main())
