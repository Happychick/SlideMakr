from fastapi.testclient import TestClient

from app import db
from app.server import app


def test_generate_requires_checkout_after_free_deck(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()

    async def fake_run_generation(text, user_id, current_user=None):
        return {"success": True, "presentation_id": "p1", "presentation_url": "https://example.com"}

    monkeypatch.setattr("app.server._run_generation", fake_run_generation)
    client = TestClient(app)

    first = client.post("/generate", json={"text": "Create 3 slides", "user_id": "guest_1"})
    second = client.post("/generate", json={"text": "Create 3 slides", "user_id": "guest_1"})

    assert first.status_code == 200
    assert second.status_code == 402
    assert second.json()["checkout_required"] is True


def test_billing_credits_endpoint_for_guest_user(monkeypatch):
    """Guests can only ever hold the free-deck flag -- they can't sign in to
    purchase, so /billing/credits for a guest reports first_deck_used, not a
    credits balance."""
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()

    client = TestClient(app)
    response = client.get("/billing/credits?user_id=guest_1")

    assert response.status_code == 200
    body = response.json()
    assert body["first_deck_used"] is False
    assert "credits" not in body


def _signed_in(monkeypatch, google_id="google_1", email="signed@example.com"):
    """Make app.server.get_current_user() return a signed-in session user."""
    user = {"google_id": google_id, "email": email, "name": "Signed In"}
    monkeypatch.setattr("app.server.get_current_user", lambda request: user)
    return user


def test_generate_402_for_guest_carries_login_required(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()

    async def fake_run_generation(text, user_id, current_user=None):
        return {"success": True, "presentation_id": "p1", "presentation_url": "https://example.com"}

    monkeypatch.setattr("app.server._run_generation", fake_run_generation)
    client = TestClient(app)

    client.post("/generate", json={"text": "Create 3 slides", "user_id": "guest_2"})
    second = client.post("/generate", json={"text": "Create 3 slides", "user_id": "guest_2"})

    assert second.status_code == 402
    body = second.json()
    assert body["checkout_required"] is True
    assert body["login_required"] is True


def test_guest_billing_user_id_is_namespaced(monkeypatch):
    """A client-supplied guest id must never resolve to a bare google_id --
    otherwise a guest could buy or consume credits for any real account."""
    from app.server import _billing_user_id

    assert _billing_user_id(None, "some_user_supplied_id") == "guest:some_user_supplied_id"
    assert _billing_user_id(None, "").startswith("guest:")
    signed_in_id = _billing_user_id({"google_id": "google_1"}, "some_user_supplied_id")
    assert signed_in_id == "google_1"


def test_checkout_requires_login_for_guest(monkeypatch):
    client = TestClient(app)

    response = client.post("/billing/checkout", json={"package_id": "credits_10", "user_id": "guest_3"})

    assert response.status_code == 401
    assert response.json()["login_required"] is True


def test_checkout_succeeds_for_signed_in_user(monkeypatch):
    _signed_in(monkeypatch, google_id="google_1")

    monkeypatch.setattr(
        "app.server.stripe_billing.create_checkout_session",
        lambda user_id, package_id, success_url, cancel_url: {"url": "https://checkout.stripe.com/session/xyz"},
    )
    client = TestClient(app)

    response = client.post("/billing/checkout", json={"package_id": "credits_10"})

    assert response.status_code == 200
    assert response.json()["url"] == "https://checkout.stripe.com/session/xyz"


def test_billing_credits_guest_returns_free_deck_status_only(monkeypatch):
    """A guest can never purchase credits, so /billing/credits for a guest
    must not leak a `credits` field -- only whether the free deck is used."""
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()

    client = TestClient(app)
    response = client.get("/billing/credits?user_id=guest_4")

    assert response.status_code == 200
    body = response.json()
    assert body["first_deck_used"] is False
    assert "credits" not in body


def test_billing_credits_signed_in_returns_credit_balance(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    _signed_in(monkeypatch, google_id="google_1")
    db.save_user("google_1", "signed@example.com", "Signed In")
    db.add_user_credits("google_1", 5)

    client = TestClient(app)
    response = client.get("/billing/credits")

    assert response.status_code == 200
    assert response.json()["credits"] == 5


def test_edit_text_gated_for_signed_in_user_with_credits(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    _signed_in(monkeypatch, google_id="google_1")
    db.save_user("google_1", "signed@example.com", "Signed In")
    db.add_user_credits("google_1", 2)

    async def fake_apply_text_edit(request, presentation_id, instruction):
        return {"success": True, "committed": True}

    monkeypatch.setattr("app.server._apply_text_edit", fake_apply_text_edit)
    client = TestClient(app)

    response = client.post("/edit-text", json={"presentation_id": "p1", "text": "make it blue"})

    assert response.status_code == 200
    assert db.get_user_credits("google_1")["credits"] == 1


def test_edit_text_402_for_signed_in_user_without_credits(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    _signed_in(monkeypatch, google_id="google_1")
    db.save_user("google_1", "signed@example.com", "Signed In")

    client = TestClient(app)
    response = client.post("/edit-text", json={"presentation_id": "p1", "text": "make it blue"})

    assert response.status_code == 402
    assert response.json()["checkout_required"] is True


def test_edit_text_402_for_guest_carries_both_flags():
    """Edits never draw on the free first deck -- that's for creation only --
    so a guest with no purchased credits always gets a 402 here, even before
    ever calling /generate."""
    client = TestClient(app)
    response = client.post("/edit-text", json={"presentation_id": "p1", "text": "make it blue", "user_id": "guest_5"})

    assert response.status_code == 402
    body = response.json()
    assert body["checkout_required"] is True
    assert body["login_required"] is True


def test_edit_text_refunds_credit_on_generation_failure(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    _signed_in(monkeypatch, google_id="google_1")
    db.save_user("google_1", "signed@example.com", "Signed In")
    db.add_user_credits("google_1", 1)

    async def failing_apply_text_edit(request, presentation_id, instruction):
        raise RuntimeError("edit_runner exploded")

    monkeypatch.setattr("app.server._apply_text_edit", failing_apply_text_edit)
    client = TestClient(app)

    response = client.post("/edit-text", json={"presentation_id": "p1", "text": "make it blue"})

    assert response.status_code == 500
    assert db.get_user_credits("google_1")["credits"] == 1


def test_generate_refunds_credit_on_generation_failure(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    _signed_in(monkeypatch, google_id="google_1")
    db.save_user("google_1", "signed@example.com", "Signed In")
    db.add_user_credits("google_1", 1)
    db.consume_generation_credit("google_1")  # use the free deck so next call spends the credit
    assert db.get_user_credits("google_1")["credits"] == 1

    async def failing_run_generation(text, user_id, current_user=None):
        raise RuntimeError("agent exploded")

    monkeypatch.setattr("app.server._run_generation", failing_run_generation)
    client = TestClient(app)

    response = client.post("/generate", json={"text": "Create 3 slides"})

    assert response.status_code == 500
    assert db.get_user_credits("google_1")["credits"] == 1


def test_generate_refunds_credit_when_result_reports_failure(monkeypatch):
    """_run_generation swallows its own exceptions and returns a plain
    {"success": False} dict instead of raising -- the refund must fire on
    that too, not only when _run_generation itself raises."""
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    _signed_in(monkeypatch, google_id="google_1")
    db.save_user("google_1", "signed@example.com", "Signed In")
    db.add_user_credits("google_1", 1)
    db.consume_generation_credit("google_1")  # use the free deck
    assert db.get_user_credits("google_1")["credits"] == 1

    async def returns_failure(text, user_id, current_user=None):
        return {"success": False, "error": "timed out"}

    monkeypatch.setattr("app.server._run_generation", returns_failure)
    client = TestClient(app)

    response = client.post("/generate", json={"text": "Create 3 slides"})

    assert response.status_code == 500
    assert db.get_user_credits("google_1")["credits"] == 1


def test_generate_refunds_credit_when_result_missing_presentation_url(monkeypatch):
    """success: True with no presentation_id/url is not a real creation --
    the credit must be refunded even though the response claims success."""
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    _signed_in(monkeypatch, google_id="google_1")
    db.save_user("google_1", "signed@example.com", "Signed In")
    db.add_user_credits("google_1", 1)
    db.consume_generation_credit("google_1")  # use the free deck
    assert db.get_user_credits("google_1")["credits"] == 1

    async def returns_success_without_deck(text, user_id, current_user=None):
        return {"success": True, "response": "ok", "presentation_id": None, "presentation_url": None}

    monkeypatch.setattr("app.server._run_generation", returns_success_without_deck)
    client = TestClient(app)

    response = client.post("/generate", json={"text": "Create 3 slides"})

    assert response.status_code == 200
    assert db.get_user_credits("google_1")["credits"] == 1


def test_edit_text_refunds_credit_when_zero_requests_committed(monkeypatch):
    """_apply_text_edit computes success = committed or success_count > 0 --
    a zero-committed-requests result is a failed edit and must be refunded."""
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    _signed_in(monkeypatch, google_id="google_1")
    db.save_user("google_1", "signed@example.com", "Signed In")
    db.add_user_credits("google_1", 1)

    async def zero_committed_edit(request, presentation_id, instruction):
        return {"committed": False, "success_count": 0, "success": False}

    monkeypatch.setattr("app.server._apply_text_edit", zero_committed_edit)
    client = TestClient(app)

    response = client.post("/edit-text", json={"presentation_id": "p1", "text": "make it blue"})

    assert response.status_code == 500
    assert db.get_user_credits("google_1")["credits"] == 1


def test_refund_never_masks_original_error_when_refund_itself_fails(monkeypatch):
    """A broken refund must never turn into an unhandled 500 that hides the
    real failure -- it should be logged and the original error shape kept."""
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    _signed_in(monkeypatch, google_id="google_1")
    db.save_user("google_1", "signed@example.com", "Signed In")
    db.add_user_credits("google_1", 1)
    db.consume_generation_credit("google_1")

    def broken_refund(user_id, reason):
        raise RuntimeError("refund path is also on fire")

    monkeypatch.setattr(db, "refund_generation_credit", broken_refund)

    async def failing_run_generation(text, user_id, current_user=None):
        raise RuntimeError("agent exploded")

    monkeypatch.setattr("app.server._run_generation", failing_run_generation)
    client = TestClient(app)

    response = client.post("/generate", json={"text": "Create 3 slides"})

    assert response.status_code == 500
    assert response.json()["error"] == "agent exploded"
