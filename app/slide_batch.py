"""
SlideMakr - Session-scoped batch buffer for narrow-tool commit-flush mode.

Mode A in the Step 15 tool-decomposition plan: each narrow tool APPENDS its
pre-built Slides API request dict to a context-local buffer, and a final
`commit_edits(presentation_id)` tool flushes the buffer to one batchUpdate
HTTP call. This preserves batching while keeping each narrow tool's schema
small.

Mode B (immediate-execute) is the alternative — each narrow tool hits the
Slides API synchronously without going through this buffer.

The active mode is selected by the `BATCH_MODE` module-level constant (or
the `SLIDEMAKR_BATCH_MODE` env var). Narrow tools check this at call time
so flipping modes is a single constant change + restart.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Mode switch
# ---------------------------------------------------------------------------

# "commit" (Mode A) or "immediate" (Mode B). Default "commit" — preserves
# batching so a 10-op edit still hits Google as one HTTP call.
BATCH_MODE: str = os.getenv("SLIDEMAKR_BATCH_MODE", "commit").strip().lower()

# Accepted modes:
#   "commit"    — Mode A: append to buffer, single batchUpdate on commit_edits (default)
#   "immediate" — Mode B: each narrow tool hits Google synchronously on call
#   "parallel"  — Mode C: append to buffer, on commit_edits split structural vs content,
#                 content fans out as parallel per-objectId batchUpdates via a thread pool
if BATCH_MODE not in ("commit", "immediate", "parallel"):
    BATCH_MODE = "commit"


# ---------------------------------------------------------------------------
# Per-presentation buffer.
#
# Earlier revisions used a contextvars.ContextVar to scope the buffer per
# asyncio context, but ADK runs each tool call in its own task (and sometimes
# thread pool), so the ContextVar was isolated between the narrow tool call
# that appended and the subsequent `commit_edits()` call that was supposed to
# drain. The buffer ended up empty at commit time and everything silently
# dropped.
#
# Fix: a plain module-level dict keyed by presentation_id, guarded by a Lock
# so concurrent tool calls on different presentations stay separate. Two
# concurrent sessions editing the SAME presentation would share a buffer,
# but our product (one voice session per deck at a time) never hits that
# path, and `commit_edits()` drains by presentation_id so interleaved
# commits just produce separate batches.
# ---------------------------------------------------------------------------

_buffer: Dict[str, List[Dict[str, Any]]] = {}
_lock = threading.Lock()


def append(presentation_id: str, request: Dict[str, Any]) -> int:
    """Append a single Slides API request dict to the buffer for this presentation.

    Returns the new length of the buffer for this presentation — useful for
    narrow tools to include in their return payload so the LLM knows how many
    pending edits are queued.
    """
    if not isinstance(request, dict) or len(request) != 1:
        raise ValueError(
            f"slide_batch.append: request must be a single-key dict, got {request!r}"
        )
    with _lock:
        _buffer.setdefault(presentation_id, []).append(request)
        return len(_buffer[presentation_id])


def drain(presentation_id: str) -> List[Dict[str, Any]]:
    """Remove and return all buffered requests for this presentation."""
    with _lock:
        return _buffer.pop(presentation_id, [])


def peek(presentation_id: str) -> List[Dict[str, Any]]:
    """Read (but do not remove) buffered requests — used for tests and debug."""
    with _lock:
        return list(_buffer.get(presentation_id, []))


def pending_count(presentation_id: str) -> int:
    """Count of buffered requests for this presentation."""
    return len(peek(presentation_id))


def clear(presentation_id: Optional[str] = None) -> None:
    """Clear buffer for one presentation, or all if presentation_id is None."""
    with _lock:
        if presentation_id is None:
            _buffer.clear()
            _known.clear()
            _snapshotted.clear()
        else:
            _buffer.pop(presentation_id, None)
            _known.pop(presentation_id, None)
            _snapshotted.discard(presentation_id)


# ---------------------------------------------------------------------------
# Known-object-ID tracking (object-ID validation for targeted edits).
#
# The edit agent used to invent objectIds and queue deleteObject/targeted edits
# against them; those failed at commit time. To reject them at call time, narrow
# tools check the target objectId against the set of IDs known for this session:
# IDs created/declared this turn (registered eagerly) plus a one-time snapshot of
# the live deck (taken lazily the first time an unfamiliar ID is seen).
#
# Same module-level-dict + Lock discipline as the buffer above (NOT ContextVar).
# ---------------------------------------------------------------------------

_known: Dict[str, set] = {}
_snapshotted: set = set()


def register_known_id(presentation_id: str, object_id: str) -> None:
    """Record an objectId created/declared this session as a valid target."""
    if not object_id:
        return
    with _lock:
        _known.setdefault(presentation_id, set()).add(object_id)


def known_ids(presentation_id: str) -> set:
    """Return a copy of the objectIds known for this presentation's session."""
    with _lock:
        return set(_known.get(presentation_id, set()))


def needs_snapshot(presentation_id: str) -> bool:
    """True if the live deck has not yet been snapshotted this session."""
    with _lock:
        return presentation_id not in _snapshotted


def store_snapshot(presentation_id: str, ids: set) -> None:
    """Merge a live-deck objectId snapshot in and mark this session snapshotted."""
    with _lock:
        _known.setdefault(presentation_id, set()).update(ids)
        _snapshotted.add(presentation_id)


def reset_known(presentation_id: str) -> None:
    """Forget known IDs + snapshot flag so the next edit turn re-snapshots fresh.

    Called after commit_edits flushes, since the live deck has changed.
    """
    with _lock:
        _known.pop(presentation_id, None)
        _snapshotted.discard(presentation_id)
