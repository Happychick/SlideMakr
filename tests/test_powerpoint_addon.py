from pathlib import Path

from fastapi.testclient import TestClient

from app.server import app


def test_powerpoint_manifest_declares_taskpane():
    manifest = Path("powerpoint_addon/manifest.xml").read_text()

    assert "SlideMakr" in manifest
    assert "Taskpane.Url" in manifest
    assert "https://slidemakr.com/powerpoint/taskpane.html" in manifest


def test_powerpoint_taskpane_has_voice_and_upload_flow():
    html = Path("powerpoint_addon/src/taskpane.html").read_text()

    assert "Office.onReady" in html
    assert "navigator.mediaDevices.getUserMedia" in html
    assert "/api/edit-pptx" in html


def test_edit_pptx_requires_upload():
    client = TestClient(app)
    response = client.post("/api/edit-pptx", data={"text": "make slide 3 better"})

    assert response.status_code == 400
    assert response.json()["error"] == "pptx file required"
