from app import db
from app.stripe_billing import CREDIT_PACKAGES, build_checkout_session_payload, handle_checkout_completed


def test_credit_packages_match_launch_pricing():
    assert CREDIT_PACKAGES["credits_10"]["credits"] == 10
    assert CREDIT_PACKAGES["credits_10"]["amount_cents"] == 499
    assert CREDIT_PACKAGES["credits_50"]["credits"] == 50
    assert CREDIT_PACKAGES["credits_50"]["amount_cents"] == 1999
    assert CREDIT_PACKAGES["credits_100"]["credits"] == 100
    assert CREDIT_PACKAGES["credits_100"]["amount_cents"] == 2999


def test_checkout_payload_omits_payment_method_types():
    payload = build_checkout_session_payload(
        user_id="u1",
        package_id="credits_10",
        success_url="https://slidemakr.com/?checkout=success",
        cancel_url="https://slidemakr.com/?checkout=cancel",
    )

    assert payload["mode"] == "payment"
    assert payload["metadata"]["user_id"] == "u1"
    assert payload["metadata"]["package_id"] == "credits_10"
    assert "payment_method_types" not in payload
    assert payload["line_items"][0]["price_data"]["unit_amount"] == 499


def test_checkout_completed_adds_credits_once(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db._memory_store["processed_checkout_sessions"] = set()
    db.save_user("u1", "u@example.com", "User")

    result = handle_checkout_completed({
        "id": "cs_test_1",
        "metadata": {"user_id": "u1", "package_id": "credits_10"},
    })
    duplicate = handle_checkout_completed({
        "id": "cs_test_1",
        "metadata": {"user_id": "u1", "package_id": "credits_10"},
    })

    assert result["credits_added"] == 10
    assert duplicate["status"] == "duplicate"
    assert db.get_user_credits("u1")["credits"] == 10


def test_first_deck_free_then_credit_deducted(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db.save_user("u1", "u@example.com", "User")

    first = db.consume_generation_credit("u1")
    second = db.consume_generation_credit("u1")
    db.add_user_credits("u1", 2)
    third = db.consume_generation_credit("u1")

    assert first == {"allowed": True, "reason": "first_deck_free"}
    assert second == {"allowed": False, "reason": "checkout_required", "credits": 0}
    assert third == {"allowed": True, "reason": "credit_used", "credits": 1}
