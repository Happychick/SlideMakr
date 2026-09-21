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


def test_aggregate_runs_reads_edit_flow_shaped_items_key():
    # edit-flow runs put per-edit cases under "edits", not "results" —
    # items_key lets the same aggregator read either shape.
    runs = [
        {"avg_overall": 0.5, "edits": [
            {"i": str(i), "overall": 0.5} for i in range(1, 7)
        ]},
        {"avg_overall": 0.7, "edits": [
            {"i": str(i), "overall": 0.7} for i in range(1, 7)
        ]},
    ]
    agg = aggregate_runs(runs, score_key="overall", id_key="i", avg_key="avg_overall",
                          items_key="edits")
    assert agg["n"] == 2
    assert set(agg["per_case"].keys()) == {str(i) for i in range(1, 7)}
    assert abs(agg["per_case"]["1"]["mean"] - 0.6) < 1e-9


def test_aggregate_runs_default_items_key_is_results():
    runs = [{"avg_overall": 1.0, "edits": [{"i": "1", "overall": 1.0}]}]
    agg = aggregate_runs(runs, score_key="overall", id_key="i", avg_key="avg_overall")
    # default items_key="results" — "edits" ignored, no cases found.
    assert agg["per_case"] == {}


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
    # EDIT_FLOW has 6 edits: 1 create turn + 6 edit turns + 6 vision calls
    # (vision defaults on) = 13 llm_turns.
    plan = run_mod.plan_budget(run_creation=False, run_edit=False, run_edit_flow=True,
                                repeat=1, has_deck=False)
    assert plan["decks"] == 1
    assert plan["llm_turns"] == 13


def test_plan_budget_edit_flow_with_deck_is_zero_decks():
    plan = run_mod.plan_budget(run_creation=False, run_edit=False, run_edit_flow=True,
                                repeat=1, has_deck=True)
    assert plan["decks"] == 0
    assert plan["llm_turns"] == 12  # no create turn, still 6 edit + 6 vision turns


def test_plan_budget_scales_with_repeat():
    plan = run_mod.plan_budget(run_creation=False, run_edit=False, run_edit_flow=True,
                                repeat=3, has_deck=False)
    assert plan["decks"] == 3
    assert plan["llm_turns"] == 39


def test_plan_budget_no_vision_drops_vision_turns():
    plan = run_mod.plan_budget(run_creation=False, run_edit=False, run_edit_flow=True,
                                repeat=1, has_deck=False, vision=False)
    assert plan["decks"] == 1
    assert plan["llm_turns"] == 7  # 1 create + 6 edits, no vision calls


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
    assert "PLAN: decks=1 llm_turns=13 budget=0" in out


# ---------------------------------------------------------------------------
# Task 1b.4: --rules-json / --rules-user / --no-prompt-rules, speed file
# ---------------------------------------------------------------------------

def test_load_rules_none_when_no_flags():
    assert run_mod.load_rules(None, None) is None


def test_load_rules_json_builds_ruleset_with_defaults_plus_file_rules(tmp_path):
    from app.quality_rules import DEFAULT_RULES

    payload = {
        "user_id": "example_user",
        "rules": [{
            "id": "user:headings_green", "text": "Headings are green.", "scope": "user",
            "check_id": "text_color", "params": {"role": "headings", "hex": "#00ff00"},
            "hard": False, "source": "feedback:2026-01-01T00:00:00",
        }],
    }
    path = tmp_path / "rules.json"
    path.write_text(__import__("json").dumps(payload))

    ruleset = run_mod.load_rules(str(path), None)
    ids = {r.id for r in ruleset.rules}
    assert ids == {r.id for r in DEFAULT_RULES} | {"user:headings_green"}


def test_load_rules_json_strict_raises_on_unknown_check_id(tmp_path):
    payload = {"rules": [{"id": "bad", "text": "x", "scope": "user", "check_id": "nonsense"}]}
    path = tmp_path / "rules.json"
    path.write_text(__import__("json").dumps(payload))
    with pytest.raises(ValueError):
        run_mod.load_rules(str(path), None)


def test_load_rules_user_delegates_to_load_rule_set(monkeypatch):
    sentinel = object()
    monkeypatch.setattr("app.quality_rules.load_rule_set", lambda uid: sentinel)
    assert run_mod.load_rules(None, "u123") is sentinel


def test_load_rules_json_accepts_prompt_only_rule_under_strict(tmp_path):
    # check_id=None means prompt/vision-only (not scored) — strict=True must
    # still accept it; Rule.__post_init__ only validates a NON-None check_id.
    payload = {"rules": [{
        "id": "user:no_clipart", "text": "Never use clipart-style stock images.",
        "scope": "user", "check_id": None, "hard": False,
        "source": "feedback:2026-01-01T00:00:00",
    }]}
    path = tmp_path / "rules.json"
    path.write_text(__import__("json").dumps(payload))

    ruleset = run_mod.load_rules(str(path), None)
    ids = {r.id for r in ruleset.rules}
    assert "user:no_clipart" in ids


def test_load_rules_json_takes_precedence_over_rules_user(tmp_path, monkeypatch):
    called = {"user": False}
    monkeypatch.setattr(
        "app.quality_rules.load_rule_set",
        lambda uid: called.__setitem__("user", True) or None,
    )
    path = tmp_path / "rules.json"
    path.write_text(__import__("json").dumps({"rules": []}))
    run_mod.load_rules(str(path), "u123")
    assert called["user"] is False


def test_write_speed_writes_median_and_per_edit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runs = [{"edits": [
        {"i": "1", "instruction": "retitle", "seconds": 10.0,
         "slowest_step": {"step": "llm_turn", "seconds": 8.0, "tool_calls": 1},
         "reasons": ["landed 1.0: ok", "speed 1.0: 10.0s vs 20s target — slowest: llm_turn 8.0s (1 tool calls)"]},
        {"i": "2", "instruction": "recolor", "seconds": 30.0,
         "slowest_step": {"step": "state_read", "seconds": 25.0, "tool_calls": None},
         "reasons": ["landed 1.0: ok", "speed 0.67: 30.0s vs 20s target — slowest: state_read 25.0s"]},
    ]}]
    path = run_mod._write_speed(runs)
    assert path is not None
    data = __import__("json").loads(open(path).read())
    assert data["median_edit_seconds"] == 20.0
    assert len(data["edits"]) == 2
    assert data["edits"][1]["speed_reason"].startswith("speed 0.67:")


def test_write_speed_returns_none_when_no_edits():
    assert run_mod._write_speed([{"edits": []}]) is None


# ---------------------------------------------------------------------------
# Fix round 1 (Task 1b.4 review): _write_speed reads the STRUCTURED
# timeline/slowest_step edit_flow_eval now persists on each edit dict,
# instead of re-parsing the human speed_reason string. Also computes
# slowest_steps_by_count so "what to fix next" is one lookup.
# ---------------------------------------------------------------------------

def test_write_speed_reads_structured_slowest_step_not_reason_string(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runs = [{"edits": [
        {
            "i": "1", "instruction": "add a bar chart", "seconds": 37.6,
            "timeline": [
                {"step": "state_read", "seconds": 1.2, "tool_calls": None},
                {"step": "llm_turn", "seconds": 29.1, "tool_calls": 2},
                {"step": "state_read", "seconds": 7.3, "tool_calls": None},
            ],
            "slowest_step": {"step": "llm_turn", "seconds": 29.1, "tool_calls": 2},
            "reasons": [
                "landed 1.0: ok",
                "speed 0.53: 37.6s vs 20s target — slowest: llm_turn 29.1s (2 tool calls)",
            ],
        },
    ]}]
    path = run_mod._write_speed(runs)
    data = __import__("json").loads(open(path).read())
    entry = data["edits"][0]
    assert entry["slowest_step"] == {"step": "llm_turn", "seconds": 29.1, "tool_calls": 2}
    assert data["slowest_steps_by_count"] == {"llm_turn": 1}


def test_write_speed_aggregates_slowest_steps_by_count_across_edits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runs = [{"edits": [
        {"i": "1", "instruction": "a", "seconds": 10.0,
         "slowest_step": {"step": "llm_turn", "seconds": 8.0, "tool_calls": 1}, "reasons": []},
        {"i": "2", "instruction": "b", "seconds": 12.0,
         "slowest_step": {"step": "llm_turn", "seconds": 9.0, "tool_calls": 1}, "reasons": []},
        {"i": "3", "instruction": "c", "seconds": 6.0,
         "slowest_step": {"step": "render", "seconds": 4.0, "tool_calls": None}, "reasons": []},
    ]}]
    path = run_mod._write_speed(runs)
    data = __import__("json").loads(open(path).read())
    assert data["slowest_steps_by_count"] == {"llm_turn": 2, "render": 1}


def test_no_prompt_rules_flag_defaults_true_and_can_be_disabled():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-prompt-rules", dest="prompt_rules", action="store_false", default=True)
    assert ap.parse_args([]).prompt_rules is True
    assert ap.parse_args(["--no-prompt-rules"]).prompt_rules is False
