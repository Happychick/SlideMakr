from fastapi.testclient import TestClient

from app.feedback import build_feedback_edit_prompt, normalize_feedback_items
from app.server import app


def test_normalize_feedback_items_accepts_comments_and_plain_strings():
    items = normalize_feedback_items([
        "Make slide 2 more visual",
        {"slide_number": 3, "text": "Add source to chart"},
    ])

    assert items == [
        {"slide_number": None, "text": "Make slide 2 more visual"},
        {"slide_number": 3, "text": "Add source to chart"},
    ]


def test_feedback_prompt_preserves_slide_specific_feedback():
    prompt = build_feedback_edit_prompt([
        {"slide_number": 2, "text": "Make this a flowchart"},
        {"slide_number": 4, "text": "Tighten the conclusion"},
    ])

    assert "slide 2" in prompt.lower()
    assert "flowchart" in prompt.lower()
    assert "slide 4" in prompt.lower()
    assert "Do not ask clarifying questions" in prompt


def test_apply_feedback_requires_presentation_id():
    client = TestClient(app)
    response = client.post("/api/comments/apply-feedback", json={"feedback": ["Fix slide 2"]})

    assert response.status_code == 400
    assert response.json()["error"] == "presentation_id required"
