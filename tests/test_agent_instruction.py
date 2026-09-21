"""
Tests for app.agent.build_instruction: the single assembly point for an
agent's system prompt (preamble + shared slide know-how + the quality-rules
block the eval actually scores).
"""

from __future__ import annotations

from app.agent import build_instruction
from app.quality_rules import DEFAULT_RULES, Rule, RuleSet


def test_build_instruction_contains_every_default_rule_text():
    instruction = build_instruction("PREAMBLE", None)
    assert "PREAMBLE" in instruction
    for rule in DEFAULT_RULES:
        assert rule.text in instruction


def test_build_instruction_defaults_to_universal_only_rule_set():
    default_instruction = build_instruction("PREAMBLE", None)
    explicit_instruction = build_instruction("PREAMBLE", RuleSet(list(DEFAULT_RULES)))
    assert default_instruction == explicit_instruction


def test_build_instruction_includes_user_rule_under_preferences_heading():
    user_rule = Rule(
        id="user:headings_green",
        text="Headings must be colored #00FF00.",
        scope="user",
        check_id="text_color",
        params={"role": "headings", "hex": "#00ff00"},
        hard=False,
        source="feedback:2026-01-01T00:00:00",
    )
    rules = RuleSet(list(DEFAULT_RULES) + [user_rule])
    instruction = build_instruction("PREAMBLE", rules)

    assert "This user's preferences" in instruction
    heading_index = instruction.index("This user's preferences")
    rule_index = instruction.index(user_rule.text)
    assert rule_index > heading_index


def test_build_instruction_with_no_user_rules_omits_preferences_heading():
    rules = RuleSet(list(DEFAULT_RULES))
    instruction = build_instruction("PREAMBLE", rules)
    assert "This user's preferences" not in instruction


def test_module_level_instructions_are_built_via_build_instruction():
    from app import agent as agent_mod

    expected = build_instruction(agent_mod.CREATION_PREAMBLE, None)
    assert agent_mod.AGENT_INSTRUCTION == expected

    expected_edit = build_instruction(agent_mod.EDIT_PREAMBLE, None)
    assert agent_mod.EDIT_INSTRUCTION == expected_edit
