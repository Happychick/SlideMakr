"""Round 2 fixes for the free-deck IP limit:

1. (Critical) Paid-user lockout: IP already seen on the free-deck branch
   must NOT block a user who has paid credits.
2. (Important) XFF spoofing: trust the hop from the right (real client is
   second-to-last for Cloud Run: ..., <real client>, <GFE>), not the first
   (attacker-controlled) hop.
3. (Important) Weak salt on Cloud Run: disable the IP limit rather than
   silently using a reversible dev-default salt in production.
"""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.server import app, _client_ip_hash


def _patch_memory(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db._memory_store.setdefault("free_deck_ips", {})
    db._memory_store["free_deck_ips"].clear()


# ============================================================================
# 1. Critical: paid user on a shared IP must not be locked out
# ============================================================================

def test_paid_user_on_seen_ip_falls_through_to_credit_branch(monkeypatch):
    _patch_memory(monkeypatch)

    # Guest burns the free deck from IP X.
    guest_result = db.consume_generation_credit("guest:burner", ip_hash="hash_x")
    assert guest_result == {"allowed": True, "reason": "first_deck_free"}

    # A signed-in user with credits, first_deck_used False, same IP X.
    db.add_user_credits("paid_user_1", 5)
    result = db.consume_generation_credit("paid_user_1", ip_hash="hash_x")

    assert result["allowed"] is True
    assert result["reason"] == "credit_used"
    assert result["credits"] == 4


# ============================================================================
# 2. Important: XFF parsed from the trusted side, not client-controlled
# ============================================================================

class _FakeHeaders(dict):
    def get(self, key, default=""):
        return super().get(key.lower(), default)


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, headers=None, client_host="203.0.113.9"):
        self.headers = _FakeHeaders({k.lower(): v for k, v in (headers or {}).items()})
        self.client = _FakeClient(client_host)


def test_xff_hashes_the_trusted_hop_not_the_first(monkeypatch):
    monkeypatch.delenv("SLIDEMAKR_XFF_TRUSTED_HOPS", raising=False)
    monkeypatch.setenv("SLIDEMAKR_IP_SALT", "test-salt-not-default")
    import hashlib

    request = _FakeRequest(headers={"X-Forwarded-For": "1.1.1.1, 9.9.9.9, 8.8.8.8"})
    result = _client_ip_hash(request)
    expected = hashlib.sha256(b"test-salt-not-default:9.9.9.9").hexdigest()
    assert result == expected


def test_xff_spoofing_the_first_hop_does_not_change_the_hash(monkeypatch):
    monkeypatch.setenv("SLIDEMAKR_IP_SALT", "test-salt-not-default")

    request_a = _FakeRequest(headers={"X-Forwarded-For": "1.1.1.1, 9.9.9.9, 8.8.8.8"})
    request_b = _FakeRequest(headers={"X-Forwarded-For": "6.6.6.6, 9.9.9.9, 8.8.8.8"})

    assert _client_ip_hash(request_a) == _client_ip_hash(request_b)


def test_rotating_first_xff_hop_does_not_grant_a_second_free_deck(monkeypatch):
    _patch_memory(monkeypatch)
    monkeypatch.setenv("SLIDEMAKR_IP_SALT", "test-salt-not-default")

    async def fake_run_generation(text, user_id, current_user=None):
        return {"success": True, "presentation_id": "p1", "presentation_url": "https://example.com"}

    monkeypatch.setattr("app.server._run_generation", fake_run_generation)
    client = TestClient(app)

    first = client.post(
        "/generate",
        json={"text": "Create 3 slides", "user_id": "guest_a"},
        headers={"X-Forwarded-For": "1.1.1.1, 9.9.9.9, 8.8.8.8"},
    )
    second = client.post(
        "/generate",
        json={"text": "Create 3 slides", "user_id": "guest_b"},
        headers={"X-Forwarded-For": "6.6.6.6, 9.9.9.9, 8.8.8.8"},
    )

    assert first.status_code == 200
    assert second.status_code == 402


# ============================================================================
# 3. Important: weak salt on Cloud Run disables the IP limit
# ============================================================================

def test_weak_salt_on_cloud_run_disables_ip_limit(monkeypatch, caplog):
    monkeypatch.delenv("SLIDEMAKR_IP_SALT", raising=False)
    monkeypatch.setenv("K_SERVICE", "slidemakr")

    request = _FakeRequest(headers={}, client_host="203.0.113.9")
    with caplog.at_level("ERROR"):
        result = _client_ip_hash(request)

    assert result == ""
    assert any("SLIDEMAKR_IP_SALT" in rec.message for rec in caplog.records)


def test_weak_salt_locally_still_hashes(monkeypatch):
    monkeypatch.delenv("SLIDEMAKR_IP_SALT", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)

    request = _FakeRequest(headers={}, client_host="203.0.113.9")
    result = _client_ip_hash(request)
    assert result != ""


def test_strong_salt_on_cloud_run_still_hashes(monkeypatch):
    monkeypatch.setenv("SLIDEMAKR_IP_SALT", "a-real-production-salt")
    monkeypatch.setenv("K_SERVICE", "slidemakr")

    request = _FakeRequest(headers={}, client_host="203.0.113.9")
    result = _client_ip_hash(request)
    assert result != ""


# ============================================================================
# CORS regex tightened to the add-on sidebar's actual host pattern
# ============================================================================

def test_cors_allows_script_googleusercontent_origin():
    client = TestClient(app)
    origin = "https://n-abc123.script.googleusercontent.com"
    response = client.options(
        "/generate",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    assert response.headers.get("access-control-allow-origin") == origin


def test_cors_rejects_non_script_googleusercontent_origin():
    client = TestClient(app)
    origin = "https://evil.googleusercontent.com.attacker.example"
    response = client.options(
        "/generate",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers.keys()}
