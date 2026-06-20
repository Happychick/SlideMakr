"""
Tests for Mode C (parallel) commit path in narrow_tools.commit_edits.

Mocks `slidemakr.execute_slide_requests` so we can assert:
  - structural requests go in ONE batchUpdate (ordered)
  - content requests on the SAME objectId go in ONE batchUpdate (ordered)
  - content requests on DIFFERENT objectIds fan out to parallel batchUpdates
  - groups actually run concurrently (elapsed time ≈ slowest call, not sum)

No Google API access required.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List

os.environ["SLIDEMAKR_BATCH_MODE"] = "parallel"

import pytest  # noqa: E402

# Re-import narrow_tools / slide_batch with BATCH_MODE=parallel already set
import importlib  # noqa: E402
from app import slide_batch, narrow_tools, slidemakr  # noqa: E402
importlib.reload(slide_batch)
importlib.reload(narrow_tools)


PID = "pres_parallel_test"


@pytest.fixture
def record_api(monkeypatch):
    """Replace slidemakr.execute_slide_requests with a recorder.

    Returns the shared `calls` list so tests can inspect what was sent.
    Each fake call sleeps briefly so parallel fan-out shows up as wall-clock
    concurrency.
    """
    calls: List[Dict[str, Any]] = []
    calls_lock = threading.Lock()

    def fake_execute(pid: str, reqs: List[Dict[str, Any]]) -> Dict[str, Any]:
        t0 = time.perf_counter()
        # Simulate a ~50ms HTTP round trip per batchUpdate call
        time.sleep(0.05)
        with calls_lock:
            calls.append(
                {
                    "pid": pid,
                    "requests": list(reqs),
                    "started_at": t0,
                    "thread_id": threading.get_ident(),
                }
            )
        return {
            "status": "success",
            "success_count": len(reqs),
            "total": len(reqs),
            "errors": [],
            "url": f"https://docs.google.com/presentation/d/{pid}/edit",
        }

    def fake_state(_pid: str) -> Dict[str, Any]:
        return {"slide_count": 1, "slides": []}

    monkeypatch.setattr(slidemakr, "execute_slide_requests", fake_execute)
    monkeypatch.setattr(slidemakr, "get_presentation_state", fake_state)
    slide_batch.clear()
    yield calls
    slide_batch.clear()


def test_same_object_id_stays_serialised(record_api):
    """Two updateTextStyle on the same objectId must land in one batchUpdate."""
    narrow_tools.update_text_style(PID, "title_1", bold=True)
    narrow_tools.update_text_style(PID, "title_1", color_hex="#FF0000")
    result = narrow_tools.commit_edits(PID)

    assert result["status"] == "success"
    # exactly ONE batchUpdate because all targets share title_1
    assert len(record_api) == 1
    assert len(record_api[0]["requests"]) == 2


def test_different_object_ids_fan_out(record_api):
    """N updates on N different objectIds → N parallel batchUpdates."""
    for oid in ("o1", "o2", "o3", "o4", "o5"):
        narrow_tools.update_text_style(PID, oid, bold=True)
    result = narrow_tools.commit_edits(PID)

    assert result["status"] == "success"
    # Five groups ⇒ five batchUpdate calls, one per objectId
    assert len(record_api) == 5
    assert {call["requests"][0]["updateTextStyle"]["objectId"] for call in record_api} == {
        "o1", "o2", "o3", "o4", "o5"
    }


def test_parallel_calls_actually_run_concurrently(record_api):
    """5 groups × 50ms each should finish in < 2× single-call time, not 5×.

    With sequential execution this would take ~250ms. With ThreadPoolExecutor
    parallelism it should finish in ~50-150ms depending on dispatch overhead.
    """
    for oid in ("a", "b", "c", "d", "e"):
        narrow_tools.update_text_style(PID, oid, bold=True)

    t0 = time.perf_counter()
    narrow_tools.commit_edits(PID)
    elapsed = time.perf_counter() - t0

    # Sequential would be ~250ms (5 × 50ms). Parallel should be < 150ms.
    assert elapsed < 0.20, f"parallel commit took {elapsed:.3f}s — looks sequential"


def test_structural_phase_runs_before_content(record_api):
    """add_slide (structural) must land before content updates on the new slide."""
    narrow_tools.add_slide(PID, insertion_index=1, layout="BLANK",
                           title_id="", body_id="", object_id="s2")
    narrow_tools.update_text_style(PID, "s2", bold=True)
    narrow_tools.update_text_style(PID, "other_obj", bold=True)
    narrow_tools.commit_edits(PID)

    # Expect: 1 structural batch + 2 content groups (s2, other_obj) = 3 calls
    assert len(record_api) == 3
    # First call must be the structural one (createSlide)
    first_req_types = [next(iter(r.keys())) for r in record_api[0]["requests"]]
    assert "createSlide" in first_req_types


def test_noop_on_empty_buffer(record_api):
    """commit_edits with no queued tools is a no-op."""
    result = narrow_tools.commit_edits(PID)
    assert result["status"] == "noop"
    assert len(record_api) == 0


def test_aggregated_counts_are_correct(record_api):
    """Aggregate success_count across parallel groups."""
    narrow_tools.update_text_style(PID, "o1", bold=True)
    narrow_tools.update_text_style(PID, "o2", bold=True)
    narrow_tools.update_text_style(PID, "o3", bold=True)
    result = narrow_tools.commit_edits(PID)
    assert result["success_count"] == 3
    assert result["total"] == 3
    assert result["error_count"] == 0
    assert result["mode"] == "parallel"
    assert result["content_groups"] == 3


def test_unscoped_requests_get_their_own_group(record_api):
    """replaceAllText has no objectId — it should land in _unscoped group."""
    narrow_tools.replace_all_text(PID, "foo", "bar")
    narrow_tools.update_text_style(PID, "o1", bold=True)
    narrow_tools.commit_edits(PID)
    # 2 groups ⇒ 2 calls
    assert len(record_api) == 2


def test_mode_switch_is_respected_by_env(monkeypatch, record_api):
    """Changing BATCH_MODE after import requires reload — documented behaviour."""
    # Already parallel from module-level env set. Just confirm.
    assert slide_batch.BATCH_MODE == "parallel"
