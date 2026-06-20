"""
Benchmark: commit-buffer (Mode A) vs immediate-execute (Mode B) for the
Step 15 narrow tools.

Runs a fixture 5-op edit against a live test presentation five times per
mode and prints a markdown table of wall-clock times + HTTP call counts.

Requires SERVICE_ACCOUNT_PATH or SERVICE_ACCOUNT_JSON to hit the Slides API.
If no credentials are available, prints a skip notice and exits 0.

Usage:
    python -m scripts.benchmark_batching

The fixture edit:
    1. add_text_box (createShape TEXT_BOX + insertText)
    2. update_text_style — bold + color
    3. set_slide_background
    4. add_shape — a ROUND_RECTANGLE
    5. set_element_color on the rectangle

In Mode A these 6 Slides requests (2 from add_text_box compound + 4 singletons)
go to Google in ONE batchUpdate call. In Mode B they go as 5 separate
batchUpdate calls (one per narrow tool) — add_text_box internally buffers
its compound in immediate-mode as well, so it's still one request.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from typing import Callable, List


def _emu(inches: float) -> int:
    return int(inches * 914_400)


def _has_credentials() -> bool:
    sa_path = os.environ.get("SERVICE_ACCOUNT_PATH", "").strip()
    sa_json = os.environ.get("SERVICE_ACCOUNT_JSON", "").strip()
    return bool(sa_path and os.path.isfile(sa_path)) or bool(sa_json)


def _run_once(mode: str, presentation_id: str) -> dict:
    """Run one fixture edit in the given mode; return timings + call count."""
    # Re-import to pick up the mode switch cleanly.
    os.environ["SLIDEMAKR_BATCH_MODE"] = mode
    # Force a clean re-import of narrow_tools + slide_batch so BATCH_MODE is re-read.
    for mod in ("app.slide_batch", "app.narrow_tools"):
        if mod in sys.modules:
            del sys.modules[mod]
    from app import narrow_tools, slide_batch  # noqa: E402
    from app import slidemakr  # noqa: E402

    slide_batch.clear(presentation_id)

    # Count HTTP calls by monkey-patching execute_slide_requests.
    http_calls = {"count": 0}
    original = slidemakr.execute_slide_requests

    def counting(pid, reqs):
        http_calls["count"] += 1
        return original(pid, reqs)

    slidemakr.execute_slide_requests = counting  # type: ignore[assignment]
    try:
        # Fixture edit
        state = slidemakr.get_presentation_state(presentation_id)
        slide_id = state["slides"][0]["slide_id"]
        shape_oid = f"bench_shape_{int(time.time() * 1000) % 100000}"
        tb_oid = f"bench_tb_{int(time.time() * 1000) % 100000}"

        start = time.perf_counter()
        narrow_tools.add_text_box(
            presentation_id, slide_id, "Benchmark",
            _emu(0.5), _emu(0.5), _emu(4), _emu(0.8), object_id=tb_oid,
        )
        narrow_tools.update_text_style(
            presentation_id, tb_oid, bold=True, color_hex="#FF0000", size_pt=18,
        )
        narrow_tools.set_slide_background(presentation_id, slide_id, "#F5F5F5")
        narrow_tools.add_shape(
            presentation_id, slide_id, "ROUND_RECTANGLE",
            _emu(5), _emu(1), _emu(3), _emu(1), object_id=shape_oid,
        )
        narrow_tools.set_element_color(
            presentation_id, shape_oid, fill_color_hex="#3B82F6",
        )
        if mode == "commit":
            result = narrow_tools.commit_edits(presentation_id)
        else:
            result = {"status": "immediate-mode, no commit needed"}
        elapsed = time.perf_counter() - start

        # Clean up the benchmark artifacts so the presentation stays usable.
        try:
            slide_batch.clear(presentation_id)
            slidemakr.execute_slide_requests(
                presentation_id,
                [{"deleteObject": {"objectId": shape_oid}},
                 {"deleteObject": {"objectId": tb_oid}}],
            )
        except Exception:
            pass

        return {
            "mode": mode,
            "elapsed_s": round(elapsed, 3),
            "http_calls": http_calls["count"] - (1 if mode == "immediate" else 0),  # don't count cleanup
            "commit_status": result.get("status"),
        }
    finally:
        slidemakr.execute_slide_requests = original  # type: ignore[assignment]


def main() -> int:
    if not _has_credentials():
        print("SKIP: no SERVICE_ACCOUNT_PATH / SERVICE_ACCOUNT_JSON set. "
              "Set one before running this benchmark.")
        return 0

    pid = os.environ.get("BENCHMARK_PRESENTATION_ID", "").strip()
    if not pid:
        print("Creating a fresh benchmark presentation…")
        from app import slidemakr  # noqa: E402
        pid, _url = slidemakr.create_presentation("Step 15 benchmark", use_template=False)

    print(f"Using presentation: {pid}")
    print()

    results: List[dict] = []
    for mode in ("commit", "immediate"):
        for i in range(5):
            try:
                r = _run_once(mode, pid)
                results.append(r)
                print(f"  {mode} run {i+1}: {r['elapsed_s']}s, {r['http_calls']} HTTP calls")
            except Exception as e:
                print(f"  {mode} run {i+1}: FAILED — {e}")

    def _stats(mode: str) -> tuple:
        runs = [r for r in results if r["mode"] == mode]
        if not runs:
            return (None, None, None, None)
        times = [r["elapsed_s"] for r in runs]
        calls = [r["http_calls"] for r in runs]
        return (
            round(statistics.mean(times), 3),
            round(statistics.median(times), 3),
            round(min(times), 3),
            int(statistics.mean(calls)),
        )

    a_mean, a_med, a_min, a_calls = _stats("commit")
    b_mean, b_med, b_min, b_calls = _stats("immediate")

    print()
    print("## Benchmark results — Mode A (commit-buffer) vs Mode B (immediate)")
    print()
    print("| Mode | Mean (s) | Median (s) | Min (s) | HTTP calls per edit |")
    print("|------|---------:|-----------:|--------:|--------------------:|")
    print(f"| A (commit-buffer) | {a_mean} | {a_med} | {a_min} | {a_calls} |")
    print(f"| B (immediate)     | {b_mean} | {b_med} | {b_min} | {b_calls} |")
    print()

    # Recommendation
    if a_mean is not None and b_mean is not None:
        if a_mean <= b_mean:
            print(f"Default: **Mode A** (commit-buffer) — {a_mean}s vs {b_mean}s.")
        else:
            print(f"Default: **Mode B** (immediate) — {b_mean}s vs {a_mean}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
