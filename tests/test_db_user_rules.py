"""Tests for per-user quality-rule storage (app/db.py) and
app.quality_rules.load_rule_set, on the in-memory fallback plus a fake
Firestore client.
"""

import logging

import pytest

from app import db
from app.quality_rules import DEFAULT_RULES, load_rule_set


@pytest.fixture(autouse=True)
def memory_fallback(monkeypatch):
    """Force the in-memory fallback and start each test with a clean slate."""
    monkeypatch.setattr(db, "_get_db", lambda: None)
    db._memory_store["user_rules"] = {}
    yield
    db._memory_store["user_rules"] = {}


def _rule_dict(**overrides):
    d = {
        "id": "headings_green",
        "text": "Headings should be green.",
        "check_id": "text_color",
        "params": {"role": "headings", "hex": "#00ff00"},
        "hard": False,
    }
    d.update(overrides)
    return d


# ---------------------------------------------------------------------------
# In-memory fallback
# ---------------------------------------------------------------------------

def test_add_then_get_round_trip_prefixes_id_and_source():
    stored = db.add_user_rule("u1", _rule_dict())

    assert stored["id"] == "user:headings_green"
    assert stored["scope"] == "user"
    assert stored["source"].startswith("feedback:")
    assert "created_at" in stored

    rules = db.get_user_rules("u1")
    assert len(rules) == 1
    assert rules[0]["id"] == "user:headings_green"


def test_add_upsert_same_id_replaces():
    db.add_user_rule("u1", _rule_dict())
    db.add_user_rule("u1", _rule_dict(text="Headings must be green, always."))

    rules = db.get_user_rules("u1")
    assert len(rules) == 1
    assert rules[0]["text"] == "Headings must be green, always."


def test_add_preserves_explicit_prefixed_id():
    stored = db.add_user_rule("u1", _rule_dict(id="user:headings_green"))
    assert stored["id"] == "user:headings_green"


def test_add_preserves_explicit_source():
    stored = db.add_user_rule("u1", _rule_dict(source="default"))
    assert stored["source"] == "default"


def test_delete_returns_true_when_existed_false_otherwise():
    db.add_user_rule("u1", _rule_dict())

    assert db.delete_user_rule("u1", "user:headings_green") is True
    assert db.get_user_rules("u1") == []
    assert db.delete_user_rule("u1", "user:headings_green") is False


def test_add_invalid_check_id_raises_value_error():
    with pytest.raises(ValueError):
        db.add_user_rule("u1", _rule_dict(check_id="not_a_real_check"))


def test_get_user_rules_empty_when_none_stored():
    assert db.get_user_rules("nobody") == []


def test_get_user_rules_caps_at_max_keeping_newest():
    from app.quality_rules import MAX_USER_RULES

    for i in range(30):
        db.add_user_rule("u1", _rule_dict(id=f"rule-{i:02d}", text=f"Rule number {i}."))

    rules = db.get_user_rules("u1")
    assert len(rules) == MAX_USER_RULES == 25

    # Newest 25 (by created_at) are kept -- the oldest 5 (rule-00..rule-04) are gone.
    kept_ids = {r["id"] for r in rules}
    assert "user:rule-00" not in kept_ids
    assert "user:rule-04" not in kept_ids
    assert "user:rule-29" in kept_ids
    assert "user:rule-05" in kept_ids


# ---------------------------------------------------------------------------
# load_rule_set
# ---------------------------------------------------------------------------

def test_load_rule_set_contains_defaults_and_user_rule():
    db.add_user_rule("u1", _rule_dict())

    rs = load_rule_set("u1")
    ids = {r.id for r in rs.rules}

    assert {r.id for r in DEFAULT_RULES} <= ids
    assert "user:headings_green" in ids


def test_load_rule_set_none_user_is_defaults_only():
    rs = load_rule_set(None)
    assert [r.id for r in rs.rules] == [r.id for r in DEFAULT_RULES]


def test_load_rule_set_drops_corrupt_stored_rule_with_warning(caplog):
    db._memory_store["user_rules"]["u1"] = {
        "user:bad": {
            "id": "user:bad",
            "text": "broken",
            "scope": "user",
            "check_id": "does_not_exist",
            "params": {},
            "hard": False,
            "source": "feedback:x",
        }
    }

    with caplog.at_level(logging.WARNING):
        rs = load_rule_set("u1")

    ids = {r.id for r in rs.rules}
    assert "user:bad" not in ids
    assert {r.id for r in DEFAULT_RULES} <= ids
    assert any("user:bad" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# Fake Firestore client
# ---------------------------------------------------------------------------

class _FakeDoc:
    def __init__(self, id, data):
        self.id = id
        self._data = data

    def to_dict(self):
        return dict(self._data)


class _FakeDocRef:
    def __init__(self, store, key):
        self._store = store
        self._key = key

    def set(self, data):
        self._store[self._key] = dict(data)

    def get(self):
        data = self._store.get(self._key)
        return _FakeDoc(self._key[-1], data or {})

    def delete(self):
        self._store.pop(self._key, None)


class _FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def order_by(self, field, **kwargs):
        docs = sorted(self._docs, key=lambda d: d.to_dict().get(field, ""))
        if kwargs.get("direction") == "DESCENDING":
            docs = list(reversed(docs))
        return _FakeQuery(docs)

    def limit(self, n):
        return _FakeQuery(self._docs[:n])

    def stream(self):
        return list(self._docs)


class _FakeCollectionRef:
    def __init__(self, store, path):
        self._store = store
        self._path = path

    def document(self, doc_id):
        return _FakeSubDocRef(self._store, self._path + (doc_id,))


class _FakeSubDocRef(_FakeDocRef):
    def collection(self, name):
        return _FakeSubCollectionRef(self._store, self._key + (name,))


class _FakeSubCollectionRef:
    def __init__(self, store, path):
        self._store = store
        self._path = path

    def document(self, doc_id):
        return _FakeDocRef(self._store, self._path + (doc_id,))

    def stream(self):
        prefix = self._path
        docs = [
            _FakeDoc(key[-1], data)
            for key, data in self._store.items()
            if key[:-1] == prefix
        ]
        return docs

    def order_by(self, field, **kwargs):
        docs = self.stream()
        docs.sort(key=lambda d: d.to_dict().get(field, ""))
        if kwargs.get("direction") == "DESCENDING":
            docs = list(reversed(docs))
        return _FakeQuery(docs)


class _FakeFirestoreClient:
    """Minimal stand-in for google.cloud.firestore.Client covering
    collection().document().collection().document().set()/get()/stream().
    """

    def __init__(self):
        self.store = {}

    def collection(self, name):
        return _FakeCollectionRef(self.store, (name,))


def test_firestore_path_writes_to_expected_location(monkeypatch):
    fake = _FakeFirestoreClient()
    monkeypatch.setattr(db, "_get_db", lambda: fake)

    stored = db.add_user_rule("u2", _rule_dict())
    assert stored["id"] == "user:headings_green"

    assert ("user_rules", "u2", "rules", "user:headings_green") in fake.store

    rules = db.get_user_rules("u2")
    assert len(rules) == 1
    assert rules[0]["id"] == "user:headings_green"

    assert db.delete_user_rule("u2", "user:headings_green") is True
    assert ("user_rules", "u2", "rules", "user:headings_green") not in fake.store
