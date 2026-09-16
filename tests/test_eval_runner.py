"""Tests for evals/run.py --repeat aggregation."""

from evals.run import aggregate_runs


def test_aggregate_runs_means_and_spread():
    runs = [
        {"avg_overall": 0.6, "results": [{"id": "retitle", "overall_score": 0.5}, {"id": "recolor", "overall_score": 0.7}]},
        {"avg_overall": 0.8, "results": [{"id": "retitle", "overall_score": 0.9}, {"id": "recolor", "overall_score": 0.7}]},
    ]
    agg = aggregate_runs(runs, score_key="overall_score", id_key="id", avg_key="avg_overall")
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
    agg = aggregate_runs(runs, score_key="overall", id_key="id", avg_key="avg_overall")
    assert agg["n"] == 2
    assert abs(agg["avg_overall"] - 0.7) < 1e-9
    assert agg["per_case"]["retitle"]["mean"] == 1.0


def test_aggregate_runs_creation_shaped_keys():
    """Creation runs use avg_overall_score / prompt_id / overall_score — not
    the edit eval's avg_overall / id / overall."""
    runs = [
        {"avg_overall_score": 0.6, "results": [{"prompt_id": "simple_4slide", "overall_score": 0.5}]},
        {"avg_overall_score": 0.8, "results": [{"prompt_id": "simple_4slide", "overall_score": 0.9}]},
    ]
    agg = aggregate_runs(runs, score_key="overall_score", id_key="prompt_id", avg_key="avg_overall_score")
    assert agg["n"] == 2
    assert abs(agg["avg_overall"] - 0.7) < 1e-9
    assert abs(agg["per_case"]["simple_4slide"]["mean"] - 0.7) < 1e-9


def test_aggregate_runs_excludes_none_scores_but_keeps_real_zero():
    runs = [
        {"avg_overall": 0.5, "results": [{"id": "retitle", "overall": None}]},
        {"avg_overall": 0.5, "results": [{"id": "retitle", "overall": 0.8}]},
        {"avg_overall": 0.5, "results": [{"id": "recolor", "overall": 0.0}]},
    ]
    agg = aggregate_runs(runs, score_key="overall", id_key="id", avg_key="avg_overall")
    # retitle: one run had a missing (None) score, excluded from mean/min/max;
    # the other run's real 0.8 is the only value counted.
    assert agg["per_case"]["retitle"]["mean"] == 0.8
    assert agg["per_case"]["retitle"]["min"] == 0.8
    assert agg["per_case"]["retitle"]["max"] == 0.8
    assert agg["per_case"]["retitle"]["missing"] == 1
    # recolor: a real 0.0 must be counted, not treated as missing.
    assert agg["per_case"]["recolor"]["mean"] == 0.0
    assert agg["per_case"]["recolor"]["missing"] == 0


def test_aggregate_runs_all_missing_scores_for_a_case():
    runs = [
        {"avg_overall": 0.5, "results": [{"id": "retitle", "overall": None}]},
        {"avg_overall": 0.5, "results": [{"id": "retitle"}]},
    ]
    agg = aggregate_runs(runs, score_key="overall", id_key="id", avg_key="avg_overall")
    stats = agg["per_case"]["retitle"]
    assert stats["mean"] is None and stats["min"] is None and stats["max"] is None
    assert stats["stdev"] == 0.0
    assert stats["missing"] == 2


def test_repeat_flag_defaults_to_one():
    import argparse

    from evals import run as run_mod

    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args([])
    assert args.repeat == 1
    assert hasattr(run_mod, "aggregate_runs")
