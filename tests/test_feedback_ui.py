"""Static checks for Task 1b.6: comment box routed through /api/feedback,
with a saved-preference line + undo link, and a persisted guest id."""

from pathlib import Path

HTML = (Path(__file__).parent.parent / "app" / "static" / "index.html").read_text()


def test_editor_text_posts_to_feedback_endpoint():
    assert "/api/feedback" in HTML
    assert "fetch('/api/feedback'" in HTML or 'fetch("/api/feedback"' in HTML


def test_comment_box_placeholder_mentions_preferences():
    assert "Tell me what to change, or what you always want" in HTML


def test_saved_preference_line_and_undo_link_present():
    assert "editorPreferenceLine" in HTML
    assert "Saved as your preference:" in HTML
    assert "undoSavedPreference" in HTML
    assert "/api/rules/" in HTML
    assert "method: 'DELETE'" in HTML or 'method: "DELETE"' in HTML
    assert "Preference removed" in HTML


def test_guest_id_persisted_in_local_storage():
    assert "slidemakr_uid" in HTML
    assert "localStorage.getItem('slidemakr_uid')" in HTML
    assert "localStorage.setItem('slidemakr_uid'" in HTML
