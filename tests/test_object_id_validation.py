"""
Tests for object-ID validation in the narrow editing tools.

The edit agent used to invent objectIds (e.g. `tb_2f49518277`, `node_…`) and
queue `deleteObject` / targeted edits against them. Those failed at commit time
with "The object could not be found", poisoning the whole batch while the agent
still reported success.

These tests pin the hard-reject behavior: a targeted tool given an objectId that
is neither in the live deck nor created earlier this session must refuse to queue
the request and hand back the real valid IDs. Self-created IDs and a fully
unavailable snapshot must NOT be blocked (fail-open).

Pure Python — `get_presentation_state` is monkeypatched, no Google API.
"""

from __future__ import annotations

import os

os.environ["SLIDEMAKR_BATCH_MODE"] = "commit"

import pytest  # noqa: E402

from app import narrow_tools  # noqa: E402
from app import slide_batch  # noqa: E402


PID = "pres_validation_deck"

DECK_STATE = {
    "slide_count": 1,
    "slides": [
        {
            "slide_id": "slide_real",
            "slide_index": 0,
            "elements": [
                {"objectId": "title_real", "type": "shape"},
                {"objectId": "body_real", "type": "shape"},
            ],
        },
    ],
}


@pytest.fixture(autouse=True)
def _clear_buffer():
    slide_batch.clear()
    yield
    slide_batch.clear()


@pytest.fixture
def deck(monkeypatch):
    """Stub get_presentation_state with a fixed deck; count snapshot calls."""
    calls = {"n": 0}

    def fake_state(pid):
        calls["n"] += 1
        return DECK_STATE

    monkeypatch.setattr(narrow_tools.slidemakr, "get_presentation_state", fake_state)
    return calls


def test_delete_unknown_object_id_is_rejected(deck):
    res = narrow_tools.delete_element(PID, "tb_invented")
    assert res["status"] == "error"
    assert "valid_object_ids" in res
    assert "title_real" in res["valid_object_ids"]
    # Nothing must be queued — the bad delete is stopped at the source.
    assert slide_batch.pending_count(PID) == 0


def test_delete_known_object_id_is_queued(deck):
    res = narrow_tools.delete_element(PID, "title_real")
    assert res["status"] == "queued"
    assert slide_batch.pending_count(PID) == 1


def test_targeted_edit_on_unknown_id_is_rejected(deck):
    res = narrow_tools.set_element_color(PID, "ghost_shape", fill_color_hex="#FF0000")
    assert res["status"] == "error"
    assert slide_batch.pending_count(PID) == 0


def test_self_created_id_not_validated_against_api(deck):
    # A shape created this session is immediately a valid target — and must NOT
    # cost a get_presentation_state round-trip.
    narrow_tools.add_shape(
        PID, slide_id="slide_real", shape_type="RECTANGLE", object_id="shp_new"
    )
    res = narrow_tools.set_element_color(PID, "shp_new", fill_color_hex="#00FF00")
    assert res["status"] == "queued"
    assert deck["n"] == 0  # snapshot never needed for self-created ids


def test_placeholder_ids_from_create_slide_are_registered(deck):
    # Placeholder objectIds declared in createSlide must count as known so the
    # creation flow (add_slide → insert_text into the title) is never blocked.
    narrow_tools.add_slide(
        PID, layout="TITLE_AND_BODY", title_id="t1", body_id="b1", object_id="slide_new"
    )
    res = narrow_tools.insert_text(PID, "t1", "Hello")
    assert res["status"] == "queued"
    assert deck["n"] == 0


def test_fail_open_when_snapshot_unavailable(monkeypatch):
    def boom(pid):
        raise RuntimeError("slides api unavailable")

    monkeypatch.setattr(narrow_tools.slidemakr, "get_presentation_state", boom)
    # Can't verify existence → don't block the edit.
    res = narrow_tools.delete_element(PID, "anything")
    assert res["status"] == "queued"


def test_commit_resets_known_ids():
    slide_batch.store_snapshot(PID, {"a", "b"})
    assert not slide_batch.needs_snapshot(PID)
    slide_batch.reset_known(PID)
    assert slide_batch.needs_snapshot(PID)
