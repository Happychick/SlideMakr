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
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["users"].clear()
    db.save_user("guest_1", "guest_1@guest.slidemakr.local", "Guest")
    db.add_user_credits("guest_1", 2)

    client = TestClient(app)
    response = client.get("/billing/credits?user_id=guest_1")

    assert response.status_code == 200
    assert response.json()["credits"] == 2
