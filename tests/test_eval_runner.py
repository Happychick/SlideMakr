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


# ---------------------------------------------------------------------------
# --edit-flow default kind + hard deck budget
# ---------------------------------------------------------------------------

import argparse

import pytest

from evals import run as run_mod


def _args(**overrides):
    defaults = dict(creation=False, edit=False, both=False, edit_flow=False,
                     deck=None, budget_decks=1, repeat=1)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_default_kind_is_edit_flow():
    run_creation, run_edit, run_edit_flow = run_mod.select_kinds(_args())
    assert (run_creation, run_edit, run_edit_flow) == (False, False, True)


def test_both_flag_still_means_creation_and_edit_only():
    run_creation, run_edit, run_edit_flow = run_mod.select_kinds(_args(both=True))
    assert (run_creation, run_edit, run_edit_flow) == (True, True, False)


def test_explicit_edit_flow_flag_selected():
    run_creation, run_edit, run_edit_flow = run_mod.select_kinds(_args(edit_flow=True))
    assert (run_creation, run_edit, run_edit_flow) == (False, False, True)


def test_plan_budget_edit_flow_default_is_one_deck_two_turns():
    # EDIT_FLOW has 6 edits: 1 create turn + 6 edit turns = 7 llm_turns.
    plan = run_mod.plan_budget(run_creation=False, run_edit=False, run_edit_flow=True,
                                repeat=1, has_deck=False)
    assert plan["decks"] == 1
    assert plan["llm_turns"] == 7


def test_plan_budget_edit_flow_with_deck_is_zero_decks():
    plan = run_mod.plan_budget(run_creation=False, run_edit=False, run_edit_flow=True,
                                repeat=1, has_deck=True)
    assert plan["decks"] == 0
    assert plan["llm_turns"] == 6  # no create turn, still 6 edit turns


def test_plan_budget_scales_with_repeat():
    plan = run_mod.plan_budget(run_creation=False, run_edit=False, run_edit_flow=True,
                                repeat=3, has_deck=False)
    assert plan["decks"] == 3
    assert plan["llm_turns"] == 21


def test_budget_refusal_exits_before_any_call(monkeypatch, capsys):
    """Over-budget must exit(2) and never call create_fn or edit_fn."""
    called = {"create": False, "edit": False}

    async def boom_create(*a, **k):
        called["create"] = True
        raise AssertionError("create_fn must not be called over budget")

    async def boom_edit(*a, **k):
        called["edit"] = True
        raise AssertionError("edit_fn must not be called over budget")

    monkeypatch.setattr("app.eval.default_generate_fn", boom_create, raising=False)
    monkeypatch.setattr("app.edit_runner.run_text_edit", boom_edit, raising=False)
    monkeypatch.setattr(
        "sys.argv", ["evals.run", "--budget-decks", "0"]
    )

    with pytest.raises(SystemExit) as exc:
        import asyncio
        asyncio.run(run_mod._main())

    assert exc.value.code == 2
    assert called["create"] is False
    assert called["edit"] is False
    out = capsys.readouterr().out
    assert "PLAN: decks=1 llm_turns=7 budget=0" in out
