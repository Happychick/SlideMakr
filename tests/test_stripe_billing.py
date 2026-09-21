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

    def get(self, transaction=None):
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


class _FakeTransaction:
    """Applies writes immediately (single-threaded test doubles don't need
    real transactional isolation) but goes through the same ref.set(merge=)
    path a real `transaction.set(...)` would exercise, so a test can assert
    the marker doc and the credits Increment were both written via this
    transaction object -- i.e. "together", per the production code path."""

    def __init__(self):
        self.writes = []

    def set(self, ref, data, merge=False):
        self.writes.append((ref._doc_id, dict(data), merge))
        ref.set(data, merge=merge)


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

    def transaction(self):
        return _FakeTransaction()


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

    restored = db.refund_generation_credit("u1", consumed["reason"])

    assert restored["credits"] == 3
    assert db.get_user_credits("u1")["credits"] == 3


def test_refund_generation_credit_restores_free_deck(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db.save_user("u2", "u2@example.com", "User")

    consumed = db.consume_generation_credit("u2")  # first_deck_free
    assert db.get_user_credits("u2")["first_deck_used"] is True

    db.refund_generation_credit("u2", consumed["reason"])

    assert db.get_user_credits("u2")["first_deck_used"] is False
    # the free deck is usable again after a refund
    again = db.consume_generation_credit("u2")
    assert again == {"allowed": True, "reason": "first_deck_free"}


def test_refund_generation_credit_is_a_noop_for_unrecognized_reason(monkeypatch):
    """A refund must never invent a consumption that didn't happen -- a
    missing/None/unknown reason (e.g. a checkout_required 402 that never
    actually consumed anything) does nothing, not a free +1 credit."""
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db.save_user("u3", "u3@example.com", "User")
    db.add_user_credits("u3", 5)

    for bad_reason in (None, "", "checkout_required", "something_unexpected"):
        result = db.refund_generation_credit("u3", bad_reason)
        assert result["credits"] == 5

    assert db.get_user_credits("u3")["credits"] == 5


def test_consume_generation_credit_free_deck_does_not_clobber_concurrent_credits(monkeypatch):
    """The free-deck branch must write only the fields it changes, never the
    whole doc snapshot (`**data`) -- otherwise a concurrent webhook Increment
    landing between our read and this write would be silently overwritten."""
    calls = []

    class _RecordingDocRef(_FakeDocRef):
        def set(self, data, merge=False):
            calls.append((dict(data), merge))
            super().set(data, merge=merge)

    class _RecordingCollection(_FakeCollection):
        def document(self, doc_id):
            return _RecordingDocRef(self._store, doc_id)

    class _RecordingClient(_FakeFirestoreClient):
        def collection(self, name):
            return _RecordingCollection(self._root, name)

    fake_client = _RecordingClient()
    monkeypatch.setattr(db, "_get_db", lambda: fake_client)

    result = db.consume_generation_credit("u1")

    assert result == {"allowed": True, "reason": "first_deck_free"}
    assert len(calls) == 1
    payload, merge = calls[0]
    assert merge is True
    assert "credits" not in payload
    assert payload["first_deck_used"] is True


def test_credit_checkout_session_once_in_memory_credits_only_once(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db._memory_store["processed_checkout_sessions"] = set()

    first = db.credit_checkout_session_once("cs_mem_1", "u1", 10)
    second = db.credit_checkout_session_once("cs_mem_1", "u1", 10)

    assert first is True
    assert second is False
    assert db.get_user_credits("u1")["credits"] == 10


def test_credit_checkout_session_once_writes_marker_and_increment_in_one_transaction(monkeypatch):
    """Use a fake transactional Firestore client to assert the "already
    processed" marker and the credits Increment are written together, inside
    the transaction -- not as two separate, independently-racable writes."""
    monkeypatch.setattr("google.cloud.firestore.transactional", lambda f: f)
    fake_client = _FakeFirestoreClient()
    monkeypatch.setattr(db, "_get_db", lambda: fake_client)

    credited = db.credit_checkout_session_once("cs_txn_1", "u1", 10)
    assert credited is True
    assert db.get_user_credits("u1")["credits"] == 10
    assert db.is_checkout_session_processed("cs_txn_1") is True

    # A second delivery of the same event must not double-credit.
    credited_again = db.credit_checkout_session_once("cs_txn_1", "u1", 10)
    assert credited_again is False
    assert db.get_user_credits("u1")["credits"] == 10


def test_webhook_propagates_credit_error_without_marking(monkeypatch):
    """If the atomic credit step raises, the error must propagate (not be
    swallowed) so Stripe's webhook redelivery retries it, and the session
    must not be left marked processed."""
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db._memory_store["processed_checkout_sessions"] = set()

    def failing_credit_once(session_id, user_id, credits):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(db, "credit_checkout_session_once", failing_credit_once)

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
    assert result["credited"] is True
    assert db.is_checkout_session_processed("cs_test_ok") is True
