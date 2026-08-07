"""
Tests for commit_edits' read-after-write verification + request fingerprint.

The enriched `verification` gives the edit agent a scoped view of the slides it
just touched (text + colours) so it can confirm each change landed; the
`request_fingerprint` lets the controller detect a repeated (non-progressing)
corrective turn. All pure-Python — Google API + state are monkeypatched.
"""

from __future__ import annotations

import pytest

from app import slide_batch, narrow_tools, slidemakr


PID = "pres_raw_test"


def _fake_execute(pid, reqs):
    return {"status": "success", "success_count": len(reqs), "total": len(reqs),
            "errors": [], "url": f"https://docs.google.com/presentation/d/{pid}/edit"}


def _state(slides):
    return {"slide_count": len(slides), "slides": slides}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # commit_edits reads slide_batch.BATCH_MODE at call time — pin Mode A for
    # these tests without a module reload (which would leak into other suites).
    monkeypatch.setattr(slide_batch, "BATCH_MODE", "commit")
    slide_batch.clear()
    yield
    slide_batch.clear()


def _patch_state(monkeypatch, slides):
    monkeypatch.setattr(slidemakr, "execute_slide_requests", _fake_execute)
    monkeypatch.setattr(slidemakr, "get_presentation_state", lambda _pid: _state(slides))


def test_verification_surfaces_touched_slide_colours(monkeypatch):
    """The agent can see the colour it set on the element it edited."""
    slides = [{
        "slide_id": "s1", "slide_index": 0,
        "elements": [{"objectId": "t1", "type": "shape", "placeholder": "TITLE",
                      "text": "Q4 Board Review", "text_color": "#635BFF"}],
    }]
    _patch_state(monkeypatch, slides)

    narrow_tools.update_text_style(PID, "t1", color_hex="#635BFF")
    result = narrow_tools.commit_edits(PID)

    v = result["verification"]
    assert v["scope"] == "touched"
    assert len(v["touched_slides"]) == 1
    el = v["touched_slides"][0]["elements"][0]
    assert el["objectId"] == "t1"
    assert el["text_color"] == "#635BFF"
    assert el["text"] == "Q4 Board Review"


def test_untouched_slide_is_excluded(monkeypatch):
    slides = [
        {"slide_id": "s1", "slide_index": 0,
         "elements": [{"objectId": "t1", "type": "shape", "text": "hi"}]},
        {"slide_id": "s2", "slide_index": 1,
         "elements": [{"objectId": "u9", "type": "shape", "text": "bye"}]},
    ]
    _patch_state(monkeypatch, slides)

    narrow_tools.update_text_style(PID, "t1", bold=True)
    result = narrow_tools.commit_edits(PID)

    touched = result["verification"]["touched_slides"]
    assert [s["slide_id"] for s in touched] == ["s1"]


def test_unscoped_request_summarises_all_slides(monkeypatch):
    """replaceAllText has no objectId, so we can't localise it → scope=all."""
    slides = [
        {"slide_id": "s1", "slide_index": 0, "elements": [{"objectId": "a", "type": "shape"}]},
        {"slide_id": "s2", "slide_index": 1, "elements": [{"objectId": "b", "type": "shape"}]},
    ]
    _patch_state(monkeypatch, slides)

    narrow_tools.replace_all_text(PID, "foo", "bar")
    result = narrow_tools.commit_edits(PID)

    v = result["verification"]
    assert v["scope"] == "all"
    assert len(v["touched_slides"]) == 2


def test_bounds_text_trimmed_and_capped(monkeypatch):
    long_text = "x" * 500
    els = [{"objectId": f"e{i}", "type": "shape", "text": long_text} for i in range(40)]
    els[0]["objectId"] = "t1"
    slides = [{"slide_id": "s1", "slide_index": 0, "elements": els}]
    _patch_state(monkeypatch, slides)

    narrow_tools.update_text_style(PID, "t1", bold=True)
    result = narrow_tools.commit_edits(PID)

    slide = result["verification"]["touched_slides"][0]
    assert len(slide["elements"]) <= narrow_tools._VERIFY_MAX_ELEMENTS
    assert all(len(e.get("text", "")) <= narrow_tools._VERIFY_MAX_TEXT for e in slide["elements"])


def test_graceful_when_elements_lack_text(monkeypatch):
    slides = [{"slide_id": "s1", "slide_index": 0,
               "elements": [{"objectId": "t1", "type": "shape"}]}]  # no 'text'
    _patch_state(monkeypatch, slides)

    narrow_tools.update_text_style(PID, "t1", bold=True)
    result = narrow_tools.commit_edits(PID)  # must not raise
    assert "verification" in result


def test_request_fingerprint_is_stable_and_discriminating(monkeypatch):
    slides = [{"slide_id": "s1", "slide_index": 0,
               "elements": [{"objectId": "t1", "type": "shape"}]}]
    _patch_state(monkeypatch, slides)

    narrow_tools.update_text_style(PID, "t1", bold=True)
    fp1 = narrow_tools.commit_edits(PID)["request_fingerprint"]

    narrow_tools.update_text_style(PID, "t1", bold=True)
    fp2 = narrow_tools.commit_edits(PID)["request_fingerprint"]

    narrow_tools.update_text_style(PID, "t1", italic=True)
    fp3 = narrow_tools.commit_edits(PID)["request_fingerprint"]

    assert fp1 == fp2            # identical request set → identical fingerprint
    assert fp1 != fp3            # different edit → different fingerprint
    assert len(fp1) == 16
