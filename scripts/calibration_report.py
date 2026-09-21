"""
Build a self-contained HTML calibration report from eval results.

Reads the newest `results/creation/*_aggregate*.json` and newest
`results/edit/*_aggregate*.json` (falling back to the newest per-run JSON for
a kind when no aggregate exists), plus every PNG in `results/calibration/`,
and writes `results/calibration/report.html`.

Usage: python -m scripts.calibration_report
"""

from __future__ import annotations

import base64
import glob
import html as html_lib
import json
import logging
import os

RESULTS_DIR_ENV = "SLIDEMAKR_RESULTS_DIR"
DEFAULT_RESULTS_DIR = "results"

# kind -> (id_key, score_key, avg_key, instruction/transcript key, png prefix)
_KIND_CONFIG = {
    "creation": {
        "id_key": "prompt_id",
        "score_key": "overall_score",
        "avg_key": "avg_overall_score",
        "text_key": "prompt_name",
        "png_prefix": "creation_",
    },
    "edit": {
        "id_key": "id",
        "score_key": "overall",
        "avg_key": "avg_overall",
        "text_key": "transcript",
        "png_prefix": "edit_",
    },
}


def _fmt(value) -> str:
    """Format a float for display; None renders as an em dash."""
    if value is None:
        return "—"
    return f"{value:.4f}"


def _load_json(path: str, skipped_notes: list[str]) -> dict | None:
    """Load a JSON file; on a truncated/corrupt file, warn, note it, and
    return None instead of raising (the caller falls back to an older file).
    """
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logging.warning("skipping unreadable results file %s: %s", path, e)
        skipped_notes.append(
            f"skipped unreadable file {os.path.basename(path)} ({e.__class__.__name__})"
        )
        return None


def _newest_readable(paths: list[str], skipped_notes: list[str]) -> tuple[str | None, dict | None]:
    """Return (path, data) for the newest file in `paths` that parses as JSON,
    skipping (and noting) any newer files that are corrupt/truncated.
    """
    for path in reversed(paths):
        data = _load_json(path, skipped_notes)
        if data is not None:
            return path, data
    return None, None


def _kind_paths(results_dir: str, kind: str) -> tuple[list[str], list[str]]:
    """Return (aggregate_paths, per_run_paths) for a kind, oldest to newest."""
    kind_dir = os.path.join(results_dir, kind)
    agg_paths = sorted(glob.glob(os.path.join(kind_dir, "*_aggregate*.json")))
    all_json = sorted(glob.glob(os.path.join(kind_dir, "*.json")))
    per_run_paths = [p for p in all_json if "_aggregate" not in os.path.basename(p)]
    return agg_paths, per_run_paths


def _deck_url(presentation_id: str | None) -> str | None:
    if not presentation_id:
        return None
    return f"https://docs.google.com/presentation/d/{presentation_id}/edit"


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png_data_uri(path: str) -> str | None:
    """Return a data: URI for a PNG file, or None if it's empty/not a PNG."""
    with open(path, "rb") as f:
        raw = f.read()
    if not raw.startswith(_PNG_MAGIC):
        return None
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _find_png(calibration_dir: str, prefix: str, case_id: str) -> str | None:
    path = os.path.join(calibration_dir, f"{prefix}{case_id}.png")
    return path if os.path.exists(path) else None


def _gather_kind(results_dir: str, kind: str) -> dict:
    """Load aggregate + per-run data for one eval kind, with a fallback note."""
    cfg = _KIND_CONFIG[kind]
    agg_paths, per_run_paths = _kind_paths(results_dir, kind)

    skipped_notes: list[str] = []
    agg_path, aggregate = _newest_readable(agg_paths, skipped_notes)
    per_run_path, per_run = _newest_readable(per_run_paths, skipped_notes)

    fallback_note = None
    if aggregate is None:
        if per_run is not None:
            fallback_note = (
                f"No aggregate file found for '{kind}' — falling back to the newest "
                f"single run ({os.path.basename(per_run_path)})."
            )
        else:
            fallback_note = f"No results found for '{kind}'."

    # Table row: prefer aggregate numbers; otherwise derive from the per-run.
    # min/max use the aggregate's TRUE per-case min/max across repeat runs
    # (already computed by evals.run.aggregate_runs), not the per-case means.
    if aggregate is not None:
        per_case = aggregate.get("per_case", {}).values()
        mins = [c.get("min") for c in per_case if c.get("min") is not None]
        maxes = [c.get("max") for c in per_case if c.get("max") is not None]
        row = {
            "kind": kind,
            "n": aggregate.get("n"),
            "mean": aggregate.get("avg_overall"),
            "min": min(mins) if mins else None,
            "max": max(maxes) if maxes else None,
        }
    elif per_run is not None:
        scores = [r.get(cfg["score_key"]) for r in per_run.get("results", [])]
        scores = [s for s in scores if s is not None]
        row = {
            "kind": kind,
            "n": 1,
            "mean": per_run.get(cfg["avg_key"]),
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
        }
    else:
        row = {"kind": kind, "n": 0, "mean": None, "min": None, "max": None}

    return {
        "aggregate": aggregate,
        "per_run": per_run,
        "fallback_note": fallback_note,
        "skipped_notes": skipped_notes,
        "row": row,
    }


def _build_cards(kind: str, data: dict, calibration_dir: str) -> list[str]:
    cfg = _KIND_CONFIG[kind]
    per_run = data["per_run"]
    aggregate = data["aggregate"]
    per_case_agg = (aggregate or {}).get("per_case", {})

    cards: list[str] = []
    for case in (per_run or {}).get("results", []):
        case_id = str(case.get(cfg["id_key"], "?"))
        text = case.get(cfg["text_key"]) or case.get("instruction") or ""
        scores = case.get("scores") or {}
        presentation_id = case.get("presentation_id")
        deck_url = _deck_url(presentation_id)
        png_path = _find_png(calibration_dir, cfg["png_prefix"], case_id)
        agg_stats = per_case_agg.get(case_id)

        e = html_lib.escape
        parts = ["<div class='card'>"]
        data_uri = _png_data_uri(png_path) if png_path else None
        if png_path and data_uri is None:
            logging.warning("skipping invalid PNG %s", png_path)
            parts.append(
                f"<p class='note'>skipped invalid image "
                f"{e(os.path.basename(png_path))}</p>"
            )
        elif data_uri:
            parts.append(f"<img src='{data_uri}' alt='{e(case_id)}'>")
        parts.append(f"<h3>{e(kind)} / {e(case_id)}</h3>")
        if text:
            parts.append(f"<p class='text'>{e(str(text))}</p>")
        if scores:
            parts.append("<table class='scores'>")
            for sub_key, sub_val in scores.items():
                parts.append(
                    f"<tr><td>{e(str(sub_key))}</td><td>{_fmt(sub_val)}</td></tr>"
                )
            parts.append("</table>")
        if agg_stats:
            parts.append(
                "<p class='agg'>aggregate mean {mean} (min {mn}, max {mx}, "
                "missing {missing})</p>".format(
                    mean=_fmt(agg_stats.get("mean")),
                    mn=_fmt(agg_stats.get("min")),
                    mx=_fmt(agg_stats.get("max")),
                    missing=agg_stats.get("missing", 0),
                )
            )
        if deck_url:
            parts.append(f"<p><a href='{e(deck_url)}'>Open deck</a></p>")
        parts.append("</div>")
        cards.append("\n".join(parts))
    return cards


_CSS = """
body { font-family: -apple-system, system-ui, sans-serif; margin: 0; padding: 24px 16px;
       background: #f7f7f8; color: #1a1a1a; }
h1 { margin-top: 0; }
table.summary { border-collapse: collapse; margin-bottom: 32px; }
table.summary th, table.summary td { border: 1px solid #ddd; padding: 6px 12px;
                                      text-align: right; }
table.summary th:first-child, table.summary td:first-child { text-align: left; }
.note { color: #a15c00; background: #fff3e0; padding: 8px 12px; border-radius: 6px;
        margin-bottom: 12px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 16px; align-items: stretch; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
.card { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 12px;
        display: flex; flex-direction: column; }
.card img { max-width: 100%; border-radius: 4px; }
.card h3 { margin: 8px 0 4px; font-size: 15px; }
.card .text { font-size: 13px; color: #444; }
table.scores { border-collapse: collapse; font-size: 12px; margin-top: 6px; }
table.scores td { padding: 2px 6px; }
.card .agg { font-size: 12px; color: #666; }
a { color: #0b57d0; text-decoration: underline; }
"""


def generate_report(results_dir: str) -> str:
    calibration_dir = os.path.join(results_dir, "calibration")

    kinds_data = {kind: _gather_kind(results_dir, kind) for kind in _KIND_CONFIG}

    rows_html = []
    for kind, data in kinds_data.items():
        row = data["row"]
        rows_html.append(
            "<tr><td>{kind}</td><td>{n}</td><td>{mean}</td><td>{mn}</td><td>{mx}</td></tr>".format(
                kind=html_lib.escape(row["kind"]),
                n=row["n"] if row["n"] is not None else "—",
                mean=_fmt(row["mean"]),
                mn=_fmt(row["min"]),
                mx=_fmt(row["max"]),
            )
        )

    note_lines = []
    for data in kinds_data.values():
        if data["fallback_note"]:
            note_lines.append(data["fallback_note"])
        note_lines.extend(data.get("skipped_notes", []))
    notes_html = "".join(
        f"<p class='note'>{html_lib.escape(note)}</p>" for note in note_lines
    )

    cards_html = []
    for kind, data in kinds_data.items():
        cards_html.extend(_build_cards(kind, data, calibration_dir))

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>SlideMakr calibration report</title>
<style>{_CSS}</style>
</head>
<body>
<h1>SlideMakr calibration report</h1>
{notes_html}
<table class="summary">
<thead><tr><th>eval</th><th>n</th><th>mean</th><th>min</th><th>max</th></tr></thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>
<div class="grid">
{''.join(cards_html)}
</div>
</body>
</html>
"""


def write_report() -> str:
    results_dir = os.environ.get(RESULTS_DIR_ENV, DEFAULT_RESULTS_DIR)
    out_dir = os.path.join(results_dir, "calibration")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "report.html")
    html_content = generate_report(results_dir)
    with open(out_path, "w") as f:
        f.write(html_content)
    return out_path


def main() -> None:
    path = write_report()
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
