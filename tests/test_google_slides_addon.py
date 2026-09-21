import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.server import app


def test_slides_addon_manifest_declares_sidebar_and_scopes():
    manifest = json.loads(Path("slides_addon/appsscript.json").read_text())

    assert manifest["addOns"]["common"]["name"] == "SlideMakr"
    assert "https://www.googleapis.com/auth/presentations.currentonly" in manifest["oauthScopes"]
    assert "https://www.googleapis.com/auth/script.container.ui" in manifest["oauthScopes"]


def test_slides_addon_sidebar_records_voice_and_posts_to_edit_audio():
    html = Path("slides_addon/Sidebar.html").read_text()

    assert "MediaRecorder" in html
    assert "/edit-audio" in html
    assert "activePresentationId" in html
    assert "X-SlideMakr-Token" in html
    # Popup handoff, not a cross-origin credentials:'include' fetch (that
    # never carries the SameSite=Lax session cookie from inside the sidebar
    # iframe).
    assert "/addon/connect" in html
    assert "credentials: 'include'" not in html
    assert "credentials:'include'" not in html


def test_code_gs_persists_token_via_properties_service():
    code = Path("slides_addon/Code.gs").read_text()

    assert "PropertiesService" in code
    assert "saveToken" in code
    assert "getToken" in code


def test_code_gs_defines_one_base_url_constant():
    code = Path("slides_addon/Code.gs").read_text()

    assert "SLIDEMAKR_BASE_URL" in code
    # No stray hardcoded hosts outside the one constant declaration.
    assert code.count("https://slidemakr") == 1


def test_addon_token_endpoint_401_for_guest():
    client = TestClient(app)
    response = client.post("/api/addon-token")

    assert response.status_code == 401
    assert response.json()["error"] == "Not logged in"


def test_addon_token_endpoint_200_for_signed_in_user(monkeypatch):
    user = {"google_id": "addon_user_1", "email": "a@example.com", "name": "A"}
    monkeypatch.setattr("app.server.get_current_user", lambda request: user)

    client = TestClient(app)
    response = client.post("/api/addon-token")

    assert response.status_code == 200
    body = response.json()
    assert body["expires_in"] == 900
    assert body["token"]
    assert db.resolve_addon_token(body["token"]) == "addon_user_1"


def _fake_request(headers=None):
    """Minimal stand-in for a Starlette Request -- _resolve_addon_user only
    reads .headers off it."""
    class _FakeRequest:
        def __init__(self, headers):
            self.headers = headers or {}
    return _FakeRequest(headers)


def test_billing_user_id_resolves_via_valid_addon_token(monkeypatch):
    from app.server import _billing_user_id

    monkeypatch.setattr("app.server.get_current_user", lambda request: None)
    token = db.issue_addon_token("addon_user_2")
    request = _fake_request({"x-slidemakr-token": token})

    resolved = _billing_user_id(None, "", request)

    assert resolved == "addon_user_2"


def test_billing_user_id_falls_back_to_guest_for_expired_or_unknown_token(monkeypatch):
    from app.server import _billing_user_id

    monkeypatch.setattr("app.server.get_current_user", lambda request: None)
    request = _fake_request({"x-slidemakr-token": "not-a-real-token"})

    resolved = _billing_user_id(None, "guest123", request)

    assert resolved == "guest:guest123"


def test_billing_user_id_goes_through_single_resolver(monkeypatch):
    """There is exactly one place add-on identity is resolved: assert
    _billing_user_id delegates to it rather than re-implementing token
    lookup."""
    from app.server import _billing_user_id

    calls = []

    def fake_resolver(request, form_or_body=None):
        calls.append((request, form_or_body))
        return "resolved_via_single_function"

    monkeypatch.setattr("app.server._resolve_addon_user", fake_resolver)
    request = _fake_request()
    body = {"token": "irrelevant"}

    resolved = _billing_user_id(None, "guest999", request, body)

    assert resolved == "resolved_via_single_function"
    assert calls == [(request, body)]


def test_addon_connect_redirects_to_login_when_signed_out():
    client = TestClient(app)
    response = client.get("/addon/connect", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert "/auth/login" in response.headers["location"]
    assert "next=/addon/connect" in response.headers["location"]


def test_addon_connect_issues_token_and_posts_message_when_signed_in(monkeypatch):
    user = {"google_id": "addon_user_3", "email": "c@example.com", "name": "C"}
    monkeypatch.setattr("app.server.get_current_user", lambda request: user)

    client = TestClient(app)
    response = client.get("/addon/connect")

    assert response.status_code == 200
    assert "postMessage" in response.text
    assert "slidemakr-token" in response.text
    assert "window.close()" in response.text


def test_issue_resolve_expiry_purge_in_memory(monkeypatch):
    # Force the in-memory fallback path regardless of Firestore availability.
    monkeypatch.setattr(db, "_get_db", lambda: None)
    db._memory_store["addon_tokens"].clear()

    token = db.issue_addon_token("mem_user")
    assert db.resolve_addon_token(token) == "mem_user"

    # Expire it manually and confirm resolve rejects + purge removes it.
    db._memory_store["addon_tokens"][token]["expires_at"] = 0
    assert db.resolve_addon_token(token) is None

    db.purge_expired_addon_tokens()
    assert token not in db._memory_store["addon_tokens"]


def test_issue_resolve_expiry_purge_with_fake_firestore(monkeypatch):
    class FakeFieldFilter:
        def __init__(self, field, op, value):
            self.field, self.op, self.value = field, op, value

    class FakeDoc:
        def __init__(self, doc_id, data):
            self.id = doc_id
            self._data = data
            self.exists = data is not None
            self.reference = None

        def to_dict(self):
            return self._data

    class FakeDocRef:
        def __init__(self, store, doc_id):
            self._store, self._id = store, doc_id

        def set(self, data):
            self._store[self._id] = data

        def get(self):
            return FakeDoc(self._id, self._store.get(self._id))

        def delete(self):
            self._store.pop(self._id, None)

    class FakeStream:
        def __init__(self, store, field, value):
            self._store, self._field, self._value = store, field, value

        def stream(self):
            for doc_id, data in list(self._store.items()):
                if data.get(self._field, 0) < self._value:
                    doc = FakeDoc(doc_id, data)
                    doc.reference = FakeDocRef(self._store, doc_id)
                    yield doc

    class FakeCollection:
        def __init__(self):
            self._store = {}

        def document(self, doc_id):
            return FakeDocRef(self._store, doc_id)

        def where(self, filter):
            return FakeStream(self._store, filter.field, filter.value)

    class FakeFirestoreClient:
        def __init__(self):
            self._collections = {}

        def collection(self, name):
            return self._collections.setdefault(name, FakeCollection())

    fake_client = FakeFirestoreClient()
    monkeypatch.setattr(db, "_get_db", lambda: fake_client)

    # google.cloud.firestore may already be imported elsewhere in the test
    # process, so patch its FieldFilter attribute in place rather than
    # sys.modules -- db.py's `from google.cloud import firestore` would
    # otherwise resolve to the real, already-cached module.
    from google.cloud import firestore as real_firestore
    monkeypatch.setattr(real_firestore, "FieldFilter", FakeFieldFilter)

    token = db.issue_addon_token("fs_user")
    assert db.resolve_addon_token(token) == "fs_user"

    coll = fake_client.collection("addon_tokens")
    data = coll.document(token).get().to_dict()
    coll.document(token).set({**data, "expires_at": 0})
    assert db.resolve_addon_token(token) is None

    db.purge_expired_addon_tokens()
    assert not coll.document(token).get().exists
