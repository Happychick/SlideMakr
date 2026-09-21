"""Tests for the whole-branch review fix wave:

1. CORS allowlist (no more allow_origins=["*"] + allow_credentials=True).
2. Add-on identity resolved on every gated route, not just /edit-audio.
3. Free deck limited per client IP, not just per client-supplied guest id.
"""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.server import app


# ============================================================================
# 1. CORS allowlist
# ============================================================================

def test_cors_rejects_untrusted_origin():
    client = TestClient(app)
    response = client.options(
        "/generate",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers.keys()}


def test_cors_allows_deployed_cloud_run_origin():
    client = TestClient(app)
    origin = "https://slidemakr-72711045873.us-central1.run.app"
    response = client.options(
        "/generate",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.headers.get("access-control-allow-origin") == origin


def test_cors_allows_googleusercontent_sidebar_origin():
    client = TestClient(app)
    origin = "https://n-abc123.slides.googleusercontent.com"
    response = client.options(
        "/generate",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.headers.get("access-control-allow-origin") == origin


# ============================================================================
# 2. Add-on identity honoured on every gated route
# ============================================================================

def _claimed_token(monkeypatch, google_id="addon_user_full"):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db._memory_store.setdefault("addon_tokens", {})
    db._memory_store["addon_tokens"].clear()
    state = "s" * 32
    token = db.issue_addon_token(google_id, state)
    db.claim_addon_token(token, state)
    return token


def test_edit_text_resolves_addon_token_identity(monkeypatch):
    token = _claimed_token(monkeypatch, "addon_user_edit_text")
    db.add_user_credits("addon_user_edit_text", 1)

    async def fake_apply_text_edit(request, presentation_id, instruction):
        return {"success": True, "committed": True}

    monkeypatch.setattr("app.server._apply_text_edit", fake_apply_text_edit)
    client = TestClient(app)

    response = client.post(
        "/edit-text",
        json={"presentation_id": "p1", "text": "make it blue"},
        headers={"x-slidemakr-token": token},
    )

    assert response.status_code == 200, response.text
    # The credit was drawn from the token's resolved user, not a fresh guest
    # (a guest would have hit the free/no-credit 402 above).
    assert db._memory_store["users"]["addon_user_edit_text"]["credits"] == 0
    assert not any(k.startswith("guest:") for k in db._memory_store["users"])


def test_billing_credits_resolves_addon_token_identity(monkeypatch):
    token = _claimed_token(monkeypatch, "addon_user_credits")
    db.add_user_credits("addon_user_credits", 5)

    client = TestClient(app)
    response = client.get("/billing/credits", headers={"x-slidemakr-token": token})

    assert response.status_code == 200
    body = response.json()
    # Resolves to the token's user, not a freshly-minted guest id.
    assert body["user_id"] == "addon_user_credits"
    assert not body["user_id"].startswith("guest:")


# ============================================================================
# 3. Free deck limited per client IP
# ============================================================================

def _patch_memory(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db._memory_store.setdefault("free_deck_ips", {})
    db._memory_store["free_deck_ips"].clear()


def test_second_guest_from_same_ip_is_refused(monkeypatch):
    _patch_memory(monkeypatch)

    async def fake_run_generation(text, user_id, current_user=None):
        return {"success": True, "presentation_id": "p1", "presentation_url": "https://example.com"}

    monkeypatch.setattr("app.server._run_generation", fake_run_generation)
    client = TestClient(app)

    first = client.post(
        "/generate",
        json={"text": "Create 3 slides", "user_id": "guest_a"},
        headers={"X-Forwarded-For": "1.2.3.4"},
    )
    second = client.post(
        "/generate",
        json={"text": "Create 3 slides", "user_id": "guest_b"},
        headers={"X-Forwarded-For": "1.2.3.4"},
    )

    assert first.status_code == 200
    assert second.status_code == 402
    body = second.json()
    assert body["checkout_required"] is True
    assert body["login_required"] is True


def test_guests_from_different_ips_both_allowed(monkeypatch):
    _patch_memory(monkeypatch)

    async def fake_run_generation(text, user_id, current_user=None):
        return {"success": True, "presentation_id": "p1", "presentation_url": "https://example.com"}

    monkeypatch.setattr("app.server._run_generation", fake_run_generation)
    client = TestClient(app)

    first = client.post(
        "/generate",
        json={"text": "Create 3 slides", "user_id": "guest_a"},
        headers={"X-Forwarded-For": "1.2.3.4"},
    )
    second = client.post(
        "/generate",
        json={"text": "Create 3 slides", "user_id": "guest_c"},
        headers={"X-Forwarded-For": "5.6.7.8"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
