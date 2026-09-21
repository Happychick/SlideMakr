"""Tests for app/feedback.py (classify_feedback, slug_rule_id) and the
/api/feedback + /api/rules/{id} endpoints in app/server.py.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db as db_mod
from app import feedback as feedback_mod
from app import server as server_mod


# ---------------------------------------------------------------------------
# Regex fast path — real, no mocking.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "fix slide 3, the title is cut off",
    "Slide 12 needs a bigger image",
    "make this bigger",
    "the color here is wrong",
    "that font is too small",
    "This looks bad",
])
def test_regex_fast_path_returns_edit_with_no_llm_call(text, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("LLM should not be called on the regex fast path")

    monkeypatch.setattr(feedback_mod, "_classify_with_llm", boom)

    result = feedback_mod.classify_feedback(text)

    assert result == {"kind": "edit", "rule_text": None, "check_id": None, "params": {}, "hard": False}


def test_empty_text_is_edit_with_no_llm_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("LLM should not be called on empty text")

    monkeypatch.setattr(feedback_mod, "_classify_with_llm", boom)

    assert feedback_mod.classify_feedback("   ") == {
        "kind": "edit", "rule_text": None, "check_id": None, "params": {}, "hard": False,
    }


# ---------------------------------------------------------------------------
# LLM path — google.genai client mocked.
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self.text = json.dumps(payload)


class _FakeModels:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self._payload)


class _FakeClient:
    def __init__(self, payload):
        self.models = _FakeModels(payload)


def test_llm_path_preference(monkeypatch):
    payload = {
        "kind": "preference",
        "rule_text": "Always make headings green.",
        "check_id": "text_color",
        "params": {"role": "headings", "hex": "#00ff00"},
        "hard": False,
    }
    fake_client = _FakeClient(payload)

    import google.genai as real_genai
    monkeypatch.setattr(real_genai, "Client", lambda: fake_client)

    result = feedback_mod.classify_feedback("I always want headings to be green")

    assert result["kind"] == "preference"
    assert result["rule_text"] == "Always make headings green."
    assert result["check_id"] == "text_color"
    assert result["params"] == {"role": "headings", "hex": "#00ff00"}
    assert fake_client.models.calls, "expected the model to be called"


def test_llm_path_rule_text_over_max_is_truncated(monkeypatch):
    payload = {"kind": "preference", "rule_text": "z" * 1000, "check_id": None}
    fake_client = _FakeClient(payload)

    import google.genai as real_genai
    from app.quality_rules import MAX_RULE_TEXT
    monkeypatch.setattr(real_genai, "Client", lambda: fake_client)

    result = feedback_mod.classify_feedback("always keep it consistent across every deck")

    assert result["kind"] == "preference"
    assert len(result["rule_text"]) == MAX_RULE_TEXT
    assert result["rule_text"] == ("z" * 1000)[:MAX_RULE_TEXT]


def test_llm_path_edit(monkeypatch):
    payload = {"kind": "edit"}
    fake_client = _FakeClient(payload)

    import google.genai as real_genai
    monkeypatch.setattr(real_genai, "Client", lambda: fake_client)

    result = feedback_mod.classify_feedback("please reconsider the overall vibe")

    assert result == {"kind": "edit", "rule_text": None, "check_id": None, "params": {}, "hard": False}


def test_llm_path_unknown_check_id_is_dropped(monkeypatch):
    payload = {"kind": "preference", "rule_text": "Do the thing.", "check_id": "not_a_real_check"}
    fake_client = _FakeClient(payload)

    import google.genai as real_genai
    monkeypatch.setattr(real_genai, "Client", lambda: fake_client)

    result = feedback_mod.classify_feedback("always do the thing for every deck")

    assert result["kind"] == "preference"
    assert result["check_id"] is None


def test_llm_path_uses_env_model_over_fallback(monkeypatch):
    payload = {"kind": "edit"}
    fake_client = _FakeClient(payload)

    import google.genai as real_genai
    monkeypatch.setattr(real_genai, "Client", lambda: fake_client)
    monkeypatch.setenv("SLIDEMAKR_CLASSIFY_MODEL", "gemini-test-model")

    feedback_mod.classify_feedback("always keep it consistent across decks")

    assert fake_client.models.calls[0]["model"] == "gemini-test-model"


# ---------------------------------------------------------------------------
# Exception -> fail-safe edit.
# ---------------------------------------------------------------------------

def test_llm_exception_falls_back_to_edit(monkeypatch):
    def boom(text):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(feedback_mod, "_classify_with_llm", boom)

    result = feedback_mod.classify_feedback("always keep headings consistent for every deck")

    assert result == {"kind": "edit", "rule_text": None, "check_id": None, "params": {}, "hard": False}


# ---------------------------------------------------------------------------
# slug_rule_id
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Headings Should Be Green", "headings-should-be-green"),
    ("  spaces   everywhere  ", "spaces-everywhere"),
    ("a/b/c", "a-b-c"),
    ("!!!", None),  # falls back to a random 8-char hex
    ("", None),
    ("x" * 100, "x" * 64),
])
def test_slug_rule_id(text, expected):
    slug = feedback_mod.slug_rule_id(text)
    assert len(slug) <= 64
    assert "/" not in slug
    if expected is None:
        assert len(slug) == 8
    else:
        assert slug == expected


# ---------------------------------------------------------------------------
# /api/feedback and /api/rules/{id} endpoints
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def memory_fallback(monkeypatch):
    monkeypatch.setattr(db_mod, "_get_db", lambda: None)
    db_mod._memory_store["user_rules"] = {}
    yield
    db_mod._memory_store["user_rules"] = {}


@pytest.fixture
def client():
    return TestClient(server_mod.app)


def _patch_apply_text_edit(monkeypatch, result=None):
    result = result or {"committed": True, "success_count": 1, "touched": {}, "success": True}

    async def fake_apply_text_edit(request, presentation_id, instruction, body=None):
        return dict(result)

    monkeypatch.setattr(server_mod, "_apply_text_edit", fake_apply_text_edit)
    return fake_apply_text_edit


def test_feedback_edit_not_saved(monkeypatch, client):
    _patch_apply_text_edit(monkeypatch)
    monkeypatch.setattr(feedback_mod, "classify_feedback", lambda text: {
        "kind": "edit", "rule_text": None, "check_id": None, "params": {}, "hard": False,
    })

    resp = client.post("/api/feedback", json={
        "presentation_id": "pid1", "text": "fix slide 2", "user_id": "guest-1",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "edit"
    assert data["saved_rule"] is None
    assert db_mod.get_user_rules("guest-1") == []


def test_feedback_preference_saved_with_user_prefixed_id(monkeypatch, client):
    _patch_apply_text_edit(monkeypatch)
    monkeypatch.setattr(feedback_mod, "classify_feedback", lambda text: {
        "kind": "preference",
        "rule_text": "Always make headings green.",
        "check_id": "text_color",
        "params": {"role": "headings", "hex": "#00ff00"},
        "hard": False,
    })

    resp = client.post("/api/feedback", json={
        "presentation_id": "pid1", "text": "always make headings green", "user_id": "user-42",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "preference"
    assert data["saved_rule"]["id"].startswith("user:")
    assert data["success"] is True

    saved = db_mod.get_user_rules("user-42")
    assert len(saved) == 1
    assert saved[0]["id"].startswith("user:")


def test_feedback_guest_with_no_user_id_applies_but_does_not_save(monkeypatch, client):
    _patch_apply_text_edit(monkeypatch)
    monkeypatch.setattr(feedback_mod, "classify_feedback", lambda text: {
        "kind": "preference",
        "rule_text": "Always make headings green.",
        "check_id": "text_color",
        "params": {"role": "headings", "hex": "#00ff00"},
        "hard": False,
    })

    resp = client.post("/api/feedback", json={"presentation_id": "pid1", "text": "always make headings green"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "preference"
    assert data["saved_rule"] is None


def test_feedback_missing_fields_400(client):
    resp = client.post("/api/feedback", json={"presentation_id": "", "text": ""})
    assert resp.status_code == 400


def test_delete_rule_requires_auth(client):
    resp = client.delete("/api/rules/user:headings_green")
    assert resp.status_code == 401


def test_delete_rule_404_when_absent(monkeypatch, client):
    monkeypatch.setattr(server_mod, "get_current_user", lambda request: {"google_id": "user-42"})

    resp = client.delete("/api/rules/user:does-not-exist")
    assert resp.status_code == 404


def test_delete_rule_200_when_present(monkeypatch, client):
    monkeypatch.setattr(server_mod, "get_current_user", lambda request: {"google_id": "user-42"})
    db_mod.add_user_rule("user-42", {"text": "Always make headings green.", "check_id": "text_color", "params": {}})

    resp = client.delete("/api/rules/user:always-make-headings-green")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert db_mod.get_user_rules("user-42") == []


# ---------------------------------------------------------------------------
# Guests can undo their own saved preference (review fix round 2).
# ---------------------------------------------------------------------------

def test_guest_saves_via_feedback_then_deletes_with_same_guest_id(monkeypatch, client):
    _patch_apply_text_edit(monkeypatch)
    monkeypatch.setattr(feedback_mod, "classify_feedback", lambda text: {
        "kind": "preference",
        "rule_text": "Always make headings green.",
        "check_id": "text_color",
        "params": {"role": "headings", "hex": "#00ff00"},
        "hard": False,
    })

    resp = client.post("/api/feedback", json={
        "presentation_id": "pid1", "text": "always make headings green", "user_id": "guest-77",
    })
    assert resp.status_code == 200
    saved_rule = resp.json()["saved_rule"]
    assert saved_rule is not None
    assert db_mod.get_user_rules("guest-77") == [saved_rule]

    resp = client.delete(f"/api/rules/{saved_rule['id']}?user_id=guest-77")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert db_mod.get_user_rules("guest-77") == []


def test_guest_delete_with_different_guest_id_is_404(monkeypatch, client):
    _patch_apply_text_edit(monkeypatch)
    monkeypatch.setattr(feedback_mod, "classify_feedback", lambda text: {
        "kind": "preference",
        "rule_text": "Always make headings green.",
        "check_id": "text_color",
        "params": {"role": "headings", "hex": "#00ff00"},
        "hard": False,
    })

    resp = client.post("/api/feedback", json={
        "presentation_id": "pid1", "text": "always make headings green", "user_id": "guest-owner",
    })
    saved_rule = resp.json()["saved_rule"]
    assert saved_rule is not None

    resp = client.delete(f"/api/rules/{saved_rule['id']}?user_id=guest-someone-else")
    assert resp.status_code == 404
    # The rule under its owner's id is untouched.
    assert db_mod.get_user_rules("guest-owner") == [saved_rule]


def test_guest_delete_with_no_id_anywhere_is_401(client):
    resp = client.delete("/api/rules/user:whatever")
    assert resp.status_code == 401
