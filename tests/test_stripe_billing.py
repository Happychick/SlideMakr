import pytest

from app import db
from app.stripe_billing import CREDIT_PACKAGES, build_checkout_session_payload, handle_checkout_completed


class _FakeDoc:
    def __init__(self, data):
        self.exists = data is not None
        self._data = data or {}

    def to_dict(self):
        return dict(self._data)


class _FakeDocRef:
    """Minimal Firestore document reference that only supports set(merge=)
    and rejects update() on a doc that doesn't exist yet -- mirroring real
    Firestore's NotFound error for update() on a missing document, which is
    exactly the bug add_user_credits used to have."""

    def __init__(self, store, doc_id):
        self._store = store
        self._doc_id = doc_id

    def get(self):
        return _FakeDoc(self._store.get(self._doc_id))

    def update(self, data):
        if self._doc_id not in self._store:
            raise Exception(f"NotFound: no document to update for {self._doc_id}")
        self._apply(data)

    def set(self, data, merge=False):
        if not merge:
            self._store[self._doc_id] = {}
        self._apply(data)

    def _apply(self, data):
        existing = self._store.setdefault(self._doc_id, {})
        from google.cloud import firestore as gcf
        for key, value in data.items():
            if isinstance(value, gcf.Increment):
                existing[key] = existing.get(key, 0) + value.value
            else:
                existing[key] = value


class _FakeCollection:
    def __init__(self, root, name):
        self._store = root.setdefault(name, {})

    def document(self, doc_id):
        return _FakeDocRef(self._store, doc_id)


class _FakeFirestoreClient:
    def __init__(self):
        self._root = {}

    def collection(self, name):
        return _FakeCollection(self._root, name)


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


def test_add_user_credits_creates_doc_for_never_seen_user(monkeypatch):
    """add_user_credits must use set(merge=True) + Increment, not update(),
    so it works for a user id Firestore has no doc for yet (e.g. a webhook
    that lands before the user's first /generate call creates one)."""
    fake_client = _FakeFirestoreClient()
    monkeypatch.setattr(db, "_get_db", lambda: fake_client)

    balance = db.add_user_credits("never_seen_user", 10)

    assert balance["credits"] == 10


def test_refund_generation_credit_restores_paid_credit(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db.save_user("u1", "u@example.com", "User")

    db.consume_generation_credit("u1")  # first_deck_free
    db.add_user_credits("u1", 3)
    consumed = db.consume_generation_credit("u1")  # credit_used, 2 left
    assert consumed == {"allowed": True, "reason": "credit_used", "credits": 2}

    restored = db.refund_generation_credit("u1")

    assert restored["credits"] == 3
    assert db.get_user_credits("u1")["credits"] == 3


def test_refund_generation_credit_restores_free_deck(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db.save_user("u2", "u2@example.com", "User")

    db.consume_generation_credit("u2")  # first_deck_free
    assert db.get_user_credits("u2")["first_deck_used"] is True

    db.refund_generation_credit("u2")

    assert db.get_user_credits("u2")["first_deck_used"] is False
    # the free deck is usable again after a refund
    again = db.consume_generation_credit("u2")
    assert again == {"allowed": True, "reason": "first_deck_free"}


def test_webhook_does_not_mark_processed_when_credit_fails(monkeypatch):
    """Verify -> check idempotency -> credit -> mark processed. If crediting
    raises, the session must NOT be marked processed, so Stripe's webhook
    redelivery can retry it instead of the credit being lost silently."""
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db._memory_store["processed_checkout_sessions"] = set()

    def failing_add_user_credits(user_id, credits):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(db, "add_user_credits", failing_add_user_credits)

    with pytest.raises(RuntimeError):
        handle_checkout_completed({
            "id": "cs_test_fail",
            "metadata": {"user_id": "u1", "package_id": "credits_10"},
        })

    assert db.is_checkout_session_processed("cs_test_fail") is False


def test_webhook_marks_processed_after_successful_credit(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db._memory_store["processed_checkout_sessions"] = set()
    db.save_user("u1", "u@example.com", "User")

    result = handle_checkout_completed({
        "id": "cs_test_ok",
        "metadata": {"user_id": "u1", "package_id": "credits_10"},
    })

    assert result["status"] == "credited"
    assert db.is_checkout_session_processed("cs_test_ok") is True
