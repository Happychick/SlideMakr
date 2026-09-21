from fastapi.testclient import TestClient

from app.server import app


def test_privacy_page_returns_html_ok():
    client = TestClient(app)
    resp = client.get("/privacy")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_privacy_page_covers_stripe_and_gaps():
    client = TestClient(app)
    resp = client.get("/privacy")
    text = resp.text

    assert "Stripe" in text
    assert "Not yet" in text


def test_privacy_page_has_no_raw_markdown_headings():
    client = TestClient(app)
    resp = client.get("/privacy")
    text = resp.text

    for line in text.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("# "), f"raw markdown heading left in output: {line!r}"
        assert not stripped.startswith("## "), f"raw markdown heading left in output: {line!r}"
