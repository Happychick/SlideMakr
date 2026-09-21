"""
Tests for app.edit_runner.build_text_edit_agent: it must reuse the model id
the creation text_agent uses (app.agent.TEXT_MODEL) rather than retyping the
literal, so there's exactly one place a model id for the text pipeline lives.
"""

from __future__ import annotations

import inspect

from app import agent as agent_mod
from app import edit_runner


def test_build_text_edit_agent_source_has_no_hardcoded_model_literal():
    src = inspect.getsource(edit_runner.build_text_edit_agent)
    # The function body must reference the imported constant / env fallback,
    # never retype the model id as a string literal.
    assert f'"{agent_mod.TEXT_MODEL}"' not in src
    assert f"'{agent_mod.TEXT_MODEL}'" not in src
    assert "TEXT_MODEL" in src


def test_build_text_edit_agent_uses_text_model_constant(monkeypatch):
    monkeypatch.delenv("SLIDEMAKR_EDIT_MODEL", raising=False)
    agent = edit_runner.build_text_edit_agent()
    assert agent.model == agent_mod.TEXT_MODEL


def test_build_text_edit_agent_honors_env_override(monkeypatch):
    monkeypatch.setenv("SLIDEMAKR_EDIT_MODEL", "gemini-override-model")
    agent = edit_runner.build_text_edit_agent()
    assert agent.model == "gemini-override-model"
