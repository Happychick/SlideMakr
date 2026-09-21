"""Tests for scripts/calibration_report.py — pure stdlib, no network."""

from __future__ import annotations

import base64
import json
import os

import pytest

from scripts import calibration_report as cr

# A 1x1 transparent PNG, just enough bytes to prove embedding works.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f)


@pytest.fixture
def results_dir(tmp_path):
    return str(tmp_path)


def _seed_creation(results_dir, with_aggregate=True):
    per_run = {
        "avg_overall_score": 0.81,
        "results": [
            {
                "prompt_id": "quarterly_report",
                "prompt_name": "Quarterly report",
                "status": "completed",
                "presentation_id": "PID_CREATE_1",
                "overall_score": 0.81,
                "scores": {"completeness": 0.9, "visual_quality": 0.7},
            }
        ],
    }
    _write_json(os.path.join(results_dir, "creation", "2026-01-01_00-00-00.json"), per_run)
    if with_aggregate:
        agg = {
            "n": 3,
            "avg_overall": 0.8,
            "per_case": {
                "quarterly_report": {
                    "mean": 0.8, "min": 0.7, "max": 0.9, "stdev": 0.1, "missing": 0,
                }
            },
        }
        _write_json(
            os.path.join(results_dir, "creation", "2026-01-02_00-00-00_aggregate_n3.json"), agg
        )
    return per_run


def _seed_edit(results_dir, with_aggregate=True):
    per_run = {
        "seed_presentation_id": "SEED",
        "avg_overall": 0.65,
        "results": [
            {
                "id": "retitle",
                "variant": "clean",
                "presentation_id": "PID_EDIT_1",
                "transcript": "change the title of slide 1 to Q4 Board Review",
                "scores": {"instruction_followed": 0.9, "usability": 0.8, "accuracy": 0.72,
                           "transcription": 1.0},
                "stt_seconds": 0.4,
                "edit_seconds": 2.1,
                "total_seconds": 2.5,
                "overall": 0.65,
            },
            {
                "id": "recolor_brand",
                "variant": "clean",
                "presentation_id": "PID_EDIT_2",
                "transcript": "recolor the bullets on slide 2 to Stripe purple",
                "scores": {"instruction_followed": None},
                "stt_seconds": 0.3,
                "edit_seconds": 1.9,
                "total_seconds": 2.2,
                "overall": None,
            },
        ],
    }
    _write_json(os.path.join(results_dir, "edit", "2026-01-01_00-00-00.json"), per_run)
    if with_aggregate:
        agg = {
            "n": 2,
            "avg_overall": 0.6,
            "per_case": {
                "retitle": {"mean": 0.65, "min": 0.6, "max": 0.7, "stdev": 0.05, "missing": 0},
                "recolor_brand": {"mean": None, "min": None, "max": None, "stdev": 0.0,
                                   "missing": 2},
            },
        }
        _write_json(os.path.join(results_dir, "edit", "2026-01-02_00-00-00_aggregate_n2.json"), agg)
    return per_run


def _seed_pngs(results_dir):
    cal_dir = os.path.join(results_dir, "calibration")
    os.makedirs(cal_dir, exist_ok=True)
    with open(os.path.join(cal_dir, "creation_quarterly_report.png"), "wb") as f:
        f.write(TINY_PNG)
    with open(os.path.join(cal_dir, "edit_retitle.png"), "wb") as f:
        f.write(TINY_PNG)


def test_top_table_has_one_row_per_kind_with_aggregate_numbers(results_dir):
    _seed_creation(results_dir)
    _seed_edit(results_dir)
    _seed_pngs(results_dir)

    html = cr.generate_report(results_dir)

    assert "creation" in html
    assert "edit" in html
    # aggregate n, mean, min, max for creation (single case: mean=min=max=0.8)
    assert "3" in html
    assert "0.8" in html


def test_card_per_case_with_instruction_transcript_scores_and_link(results_dir):
    _seed_creation(results_dir)
    _seed_edit(results_dir)
    _seed_pngs(results_dir)

    html = cr.generate_report(results_dir)

    # creation card
    assert "quarterly_report" in html
    assert "completeness" in html
    assert "0.9" in html
    assert "https://docs.google.com/presentation/d/PID_CREATE_1/edit" in html

    # edit card: transcript + sub-scores + deck link
    assert "retitle" in html
    assert "change the title of slide 1 to Q4 Board Review" in html
    assert "instruction_followed" in html
    assert "https://docs.google.com/presentation/d/PID_EDIT_1/edit" in html


def test_png_embedded_as_data_uri(results_dir):
    _seed_creation(results_dir)
    _seed_edit(results_dir)
    _seed_pngs(results_dir)

    html = cr.generate_report(results_dir)

    assert "data:image/png;base64," in html
    expected_b64 = base64.b64encode(TINY_PNG).decode("ascii")
    assert expected_b64 in html


def test_none_mean_renders_as_em_dash(results_dir):
    _seed_creation(results_dir)
    _seed_edit(results_dir)  # recolor_brand aggregate mean is None
    _seed_pngs(results_dir)

    html = cr.generate_report(results_dir)

    assert "—" in html


def test_fallback_message_when_no_aggregate_present(results_dir):
    _seed_creation(results_dir, with_aggregate=False)
    _seed_edit(results_dir, with_aggregate=False)
    _seed_pngs(results_dir)

    html = cr.generate_report(results_dir)

    # per-run data still renders
    assert "quarterly_report" in html
    assert "retitle" in html
    # and the page says it fell back to a single run
    assert "fallback" in html.lower() or "no aggregate" in html.lower()


def test_write_report_writes_file_and_respects_env_var(results_dir, monkeypatch):
    _seed_creation(results_dir)
    _seed_edit(results_dir)
    _seed_pngs(results_dir)

    monkeypatch.setenv("SLIDEMAKR_RESULTS_DIR", results_dir)
    path = cr.write_report()

    assert path == os.path.join(results_dir, "calibration", "report.html")
    assert os.path.exists(path)
    with open(path) as f:
        content = f.read()
    assert "quarterly_report" in content


def test_generate_report_grid_css_present(results_dir):
    _seed_creation(results_dir)
    _seed_edit(results_dir)
    _seed_pngs(results_dir)

    html = cr.generate_report(results_dir)

    assert "display: grid" in html or "display:grid" in html
    assert "align-items: stretch" in html or "align-items:stretch" in html
    assert "900px" in html


def test_top_table_uses_true_min_max_not_mean(results_dir):
    # Aggregate case: mean 0.65 but true min/max across runs are 0.6/0.75 —
    # the table must show the true spread, not min=max=mean. Use a distinct
    # max (0.75) so the assertion can't accidentally pass via a per-case
    # card's "aggregate mean/min/max" line, which happens to reuse the same
    # 0.6/0.7 numbers in other fixtures.
    agg = {
        "n": 4,
        "avg_overall": 0.65,
        "per_case": {
            "retitle": {"mean": 0.65, "min": 0.6, "max": 0.75, "stdev": 0.05, "missing": 0},
        },
    }
    _write_json(os.path.join(results_dir, "edit", "2026-01-02_00-00-00_aggregate_n4.json"), agg)
    per_run = {
        "avg_overall": 0.65,
        "results": [
            {
                "id": "retitle", "variant": "clean", "presentation_id": "PID_EDIT_1",
                "transcript": "change the title", "scores": {"instruction_followed": 0.9},
                "overall": 0.65,
            }
        ],
    }
    _write_json(os.path.join(results_dir, "edit", "2026-01-01_00-00-00.json"), per_run)

    html = cr.generate_report(results_dir)

    table_html = html.split("<table class=\"summary\">")[1].split("</table>")[0]
    assert "0.6000" in table_html
    assert "0.7500" in table_html
    # the wrong (mean-based) values must NOT appear as the min/max columns
    assert "0.6500</td><td>0.6500" not in table_html


def test_corrupt_newest_file_falls_back_and_notes_it(results_dir):
    # Newest aggregate is corrupt JSON; older per-run JSON is valid.
    kind_dir = os.path.join(results_dir, "creation")
    os.makedirs(kind_dir, exist_ok=True)
    _write_json(os.path.join(kind_dir, "2026-01-01_00-00-00.json"), {
        "avg_overall_score": 0.81,
        "results": [
            {
                "prompt_id": "quarterly_report", "presentation_id": "PID_CREATE_1",
                "overall_score": 0.81, "scores": {"completeness": 0.9},
            }
        ],
    })
    corrupt_path = os.path.join(kind_dir, "2026-01-02_00-00-00_aggregate_n3.json")
    with open(corrupt_path, "w") as f:
        f.write("{not valid json!!")

    _seed_edit(results_dir)
    _seed_pngs(results_dir)

    html = cr.generate_report(results_dir)

    assert "skipped unreadable file" in html.lower()
    assert os.path.basename(corrupt_path) in html
    # valid per-run data still renders
    assert "quarterly_report" in html


def test_png_with_bad_magic_bytes_is_skipped_with_note(results_dir):
    _seed_creation(results_dir)
    _seed_edit(results_dir)
    cal_dir = os.path.join(results_dir, "calibration")
    os.makedirs(cal_dir, exist_ok=True)
    # Not a real PNG.
    with open(os.path.join(cal_dir, "creation_quarterly_report.png"), "wb") as f:
        f.write(b"not a real png")
    with open(os.path.join(cal_dir, "edit_retitle.png"), "wb") as f:
        f.write(TINY_PNG)

    html = cr.generate_report(results_dir)

    assert "data:image/png;base64," in html  # the valid one still embeds
    assert "quarterly_report" in html
    assert "skipped" in html.lower() or "invalid" in html.lower()
