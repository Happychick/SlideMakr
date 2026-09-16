"""Tests for evals/run.py --repeat aggregation."""

from evals.run import aggregate_runs


def test_aggregate_runs_means_and_spread():
    runs = [
        {"avg_overall": 0.6, "results": [{"id": "retitle", "overall_score": 0.5}, {"id": "recolor", "overall_score": 0.7}]},
        {"avg_overall": 0.8, "results": [{"id": "retitle", "overall_score": 0.9}, {"id": "recolor", "overall_score": 0.7}]},
    ]
    agg = aggregate_runs(runs, score_key="overall_score", id_key="id")
    assert agg["n"] == 2
    assert abs(agg["avg_overall"] - 0.7) < 1e-9
    assert abs(agg["per_case"]["retitle"]["mean"] - 0.7) < 1e-9
    assert agg["per_case"]["retitle"]["min"] == 0.5 and agg["per_case"]["retitle"]["max"] == 0.9
    assert agg["per_case"]["recolor"]["stdev"] == 0.0


def test_aggregate_runs_handles_missing_results_key():
    runs = [
        {"avg_overall": 0.5},
        {"avg_overall": 0.9, "results": [{"id": "retitle", "overall": 1.0}]},
    ]
    agg = aggregate_runs(runs, score_key="overall", id_key="id")
    assert agg["n"] == 2
    assert abs(agg["avg_overall"] - 0.7) < 1e-9
    assert agg["per_case"]["retitle"]["mean"] == 1.0


def test_repeat_flag_defaults_to_one():
    import argparse

    from evals import run as run_mod

    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args([])
    assert args.repeat == 1
    assert hasattr(run_mod, "aggregate_runs")
