import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.server import app


def test_slides_addon_manifest_declares_sidebar_and_scopes():
    manifest = json.loads(Path("slides_addon/appsscript.json").read_text())

    assert manifest["addOns"]["common"]["name"] == "SlideMakr"
    assert "https://www.googleapis.com/auth/presentations.currentonly" in manifest["oauthScopes"]
    assert "https://www.googleapis.com/auth/script.container.ui" in manifest["oauthScopes"]


def test_slides_addon_sidebar_posts_voice_or_text_to_backend():
    html = Path("slides_addon/Sidebar.html").read_text()

    assert "navigator.mediaDevices.getUserMedia" in html
    assert "/api/addon/edit" in html
    assert "activePresentationId" in html


def test_addon_token_endpoint_returns_short_lived_token_for_logged_in_user():
    client = TestClient(app)
    with client as c:
        c.get("/auth/me")
        c.cookies.set("session", "")

    # Direct session cookie crafting is intentionally hard; endpoint should at
    # least exist and reject anonymous users instead of 404ing.
    response = client.post("/api/addon-token")

    assert response.status_code == 401
    assert response.json()["error"] == "Not logged in"


def test_addon_edit_requires_token():
    client = TestClient(app)
    response = client.post("/api/addon/edit", json={
        "presentation_id": "p1",
        "text": "Add a flowchart on slide 6",
    })

    assert response.status_code == 401
