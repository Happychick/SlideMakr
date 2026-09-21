import json
import re
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
    # Issued but never claimed -- not usable for billing identity.
    assert db.resolve_addon_token(body["token"]) is None


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
    monkeypatch.setattr(db, "_get_db", lambda: None)
    db._memory_store["addon_tokens"].clear()
    state = "1" * 32
    token = db.issue_addon_token("addon_user_2", state)
    assert db.claim_addon_token(token, state) is True
    request = _fake_request({"x-slidemakr-token": token})

    resolved = _billing_user_id(None, "", request)

    assert resolved == "addon_user_2"


def test_unclaimed_addon_token_does_not_resolve_billing_identity(monkeypatch):
    """An unclaimed token must not authenticate as the victim it was minted
    for -- this is what makes `claim` meaningful rather than decorative."""
    from app.server import _billing_user_id

    monkeypatch.setattr("app.server.get_current_user", lambda request: None)
    monkeypatch.setattr(db, "_get_db", lambda: None)
    db._memory_store["addon_tokens"].clear()
    token = db.issue_addon_token("victim_user", "z" * 32)  # never claimed
    request = _fake_request({"x-slidemakr-token": token})

    resolved = _billing_user_id(None, "guest_fallback", request)

    assert resolved == "guest:guest_fallback"
    assert resolved != "victim_user"


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


def test_addon_connect_get_mints_no_token(monkeypatch):
    """GET is a consent screen only -- clicking nothing must mint nothing."""
    user = {"google_id": "addon_user_3", "email": "c@example.com", "name": "C"}
    monkeypatch.setattr("app.server.get_current_user", lambda request: user)
    monkeypatch.setattr(db, "_get_db", lambda: None)
    db._memory_store["addon_tokens"].clear()

    client = TestClient(app)
    response = client.get("/addon/connect", params={"state": "b" * 32})

    assert response.status_code == 200
    assert "Connect" in response.text
    assert "postMessage" not in response.text
    assert db._memory_store["addon_tokens"] == {}


def test_addon_connect_post_without_csrf_is_403(monkeypatch):
    user = {"google_id": "addon_user_4", "email": "d@example.com", "name": "D"}
    monkeypatch.setattr("app.server.get_current_user", lambda request: user)
    monkeypatch.setattr(db, "_get_db", lambda: None)
    db._memory_store["addon_tokens"].clear()

    client = TestClient(app)
    response = client.post("/addon/connect", data={"state": "c" * 32})

    assert response.status_code == 403
    assert db._memory_store["addon_tokens"] == {}


def test_addon_connect_post_wrong_csrf_is_403(monkeypatch):
    user = {"google_id": "addon_user_5", "email": "e@example.com", "name": "E"}
    monkeypatch.setattr("app.server.get_current_user", lambda request: user)
    monkeypatch.setattr(db, "_get_db", lambda: None)
    db._memory_store["addon_tokens"].clear()

    client = TestClient(app)
    state = "d" * 32
    get_response = client.get("/addon/connect", params={"state": state})
    assert get_response.status_code == 200

    response = client.post("/addon/connect", data={"csrf": "not-the-real-token", "state": state})

    assert response.status_code == 403
    assert db._memory_store["addon_tokens"] == {}


def test_addon_connect_post_valid_csrf_issues_token_and_posts_message(monkeypatch):
    user = {"google_id": "addon_user_6", "email": "f@example.com", "name": "F"}
    monkeypatch.setattr("app.server.get_current_user", lambda request: user)
    monkeypatch.setattr(db, "_get_db", lambda: None)
    db._memory_store["addon_tokens"].clear()

    client = TestClient(app)
    state = "e" * 32
    get_response = client.get("/addon/connect", params={"state": state})
    csrf_match = re.search(r'name="csrf" value="([^"]+)"', get_response.text)
    assert csrf_match, "consent page must carry a hidden csrf field"
    csrf_token = csrf_match.group(1)

    response = client.post("/addon/connect", data={"csrf": csrf_token, "state": state})

    assert response.status_code == 200
    assert "postMessage" in response.text
    assert "slidemakr-token" in response.text
    assert "window.close()" in response.text
    assert state in response.text
    assert len(db._memory_store["addon_tokens"]) == 1


def test_issue_resolve_expiry_purge_in_memory(monkeypatch):
    # Force the in-memory fallback path regardless of Firestore availability.
    monkeypatch.setattr(db, "_get_db", lambda: None)
    db._memory_store["addon_tokens"].clear()

    state = "9" * 32
    token = db.issue_addon_token("mem_user", state)
    # Unclaimed -- resolve must refuse it even though it isn't expired.
    assert db.resolve_addon_token(token) is None

    assert db.claim_addon_token(token, state) is True
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

        def get(self, transaction=None):
            return FakeDoc(self._id, self._store.get(self._id))

        def update(self, data):
            self._store.setdefault(self._id, {}).update(data)

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

    class FakeTransaction:
        def update(self, doc_ref, data):
            doc_ref.update(data)

    class FakeFirestoreClient:
        def __init__(self):
            self._collections = {}

        def collection(self, name):
            return self._collections.setdefault(name, FakeCollection())

        def transaction(self):
            return FakeTransaction()

    fake_client = FakeFirestoreClient()
    monkeypatch.setattr(db, "_get_db", lambda: fake_client)

    # google.cloud.firestore may already be imported elsewhere in the test
    # process, so patch its attributes in place rather than sys.modules --
    # db.py's `from google.cloud import firestore` would otherwise resolve
    # to the real, already-cached module. `transactional` is patched to a
    # plain pass-through since the fake transaction object doesn't
    # implement real Firestore's retry/commit protocol.
    from google.cloud import firestore as real_firestore
    monkeypatch.setattr(real_firestore, "FieldFilter", FakeFieldFilter)
    monkeypatch.setattr(real_firestore, "transactional", lambda f: f)

    state = "8" * 32
    token = db.issue_addon_token("fs_user", state)
    # Unclaimed -- resolve must refuse it even though it isn't expired.
    assert db.resolve_addon_token(token) is None
    assert db.claim_addon_token(token, state) is True
    assert db.resolve_addon_token(token) == "fs_user"

    coll = fake_client.collection("addon_tokens")
    data = coll.document(token).get().to_dict()
    coll.document(token).set({**data, "expires_at": 0})
    assert db.resolve_addon_token(token) is None

    db.purge_expired_addon_tokens()
    assert not coll.document(token).get().exists


def test_addon_connect_400_when_state_missing_or_invalid(monkeypatch):
    user = {"google_id": "state_user_1", "email": "s1@example.com", "name": "S1"}
    monkeypatch.setattr("app.server.get_current_user", lambda request: user)
    client = TestClient(app)

    missing = client.get("/addon/connect")
    assert missing.status_code == 400

    too_short = client.get("/addon/connect", params={"state": "short"})
    assert too_short.status_code == 400

    bad_chars = client.get("/addon/connect", params={"state": "!!!" + "a" * 20})
    assert bad_chars.status_code == 400


def test_addon_connect_200_with_state_embeds_it_in_consent_page(monkeypatch):
    user = {"google_id": "state_user_2", "email": "s2@example.com", "name": "S2"}
    monkeypatch.setattr("app.server.get_current_user", lambda request: user)
    client = TestClient(app)

    state = "a" * 32
    response = client.get("/addon/connect", params={"state": state})

    assert response.status_code == 200
    assert state in response.text
    assert "s2@example.com" in response.text
    # No token minted or handed off on GET -- that only happens after the
    # explicit consent-click POST.
    assert "postMessage" not in response.text


def test_claim_addon_token_wrong_state_is_403(monkeypatch):
    monkeypatch.setattr(db, "_get_db", lambda: None)
    db._memory_store["addon_tokens"].clear()
    token = db.issue_addon_token("claim_user_1", "correct-state-0123456789")

    client = TestClient(app)
    response = client.post("/api/addon-token/claim", json={"token": token, "state": "wrong-state"})

    assert response.status_code == 403


def test_claim_addon_token_right_state_once_then_403(monkeypatch):
    monkeypatch.setattr(db, "_get_db", lambda: None)
    db._memory_store["addon_tokens"].clear()
    state = "correct-state-0123456789"
    token = db.issue_addon_token("claim_user_2", state)

    client = TestClient(app)
    first = client.post("/api/addon-token/claim", json={"token": token, "state": state})
    assert first.status_code == 200
    assert first.json()["claimed"] is True

    second = client.post("/api/addon-token/claim", json={"token": token, "state": state})
    assert second.status_code == 403


def test_sidebar_verifies_origin_and_state_before_saving_token():
    html_src = Path("slides_addon/Sidebar.html").read_text()

    assert "event.origin !== backendUrl" in html_src
    assert "event.data.state !== expectedConnectState" in html_src
    assert "/api/addon-token/claim" in html_src
    assert "getRandomValues" in html_src


def test_addon_connect_post_response_is_valid_js_not_html_escaped(monkeypatch):
    """html.escape(json.dumps(...)) inside a <script> block turns `"` into
    `&quot;`, which is a SyntaxError in the browser -- the handoff script
    must use a JS-safe string literal instead."""
    user = {"google_id": "addon_user_7", "email": "g@example.com", "name": "G"}
    monkeypatch.setattr("app.server.get_current_user", lambda request: user)
    monkeypatch.setattr(db, "_get_db", lambda: None)
    db._memory_store["addon_tokens"].clear()

    client = TestClient(app)
    state = "9" * 32
    get_response = client.get("/addon/connect", params={"state": state})
    csrf_match = re.search(r'name="csrf" value="([^"]+)"', get_response.text)
    csrf_token = csrf_match.group(1)

    response = client.post("/addon/connect", data={"csrf": csrf_token, "state": state})

    assert response.status_code == 200
    assert "&quot;" not in response.text
    assert 'token: "' in response.text
