from app import db


def test_save_presentation_metrics_records_adherence_fields(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["presentation_metrics"].clear()

    db.save_presentation_metrics(
        presentation_id="p1",
        user_id="u1",
        instructions="Create 6 slides and put a flowchart on slide 6.",
        slide_count=6,
        request_count=10,
        success_count=10,
        error_count=0,
        duration_seconds=12.3,
        instruction_contract={"slide_count": 6, "requirements": []},
        adherence_result={
            "instruction_adherence_score": 0.5,
            "missing_element_errors": ["slide_6:flowchart"],
            "wrong_slide_errors": ["flowchart_expected_slide_6_found_slide_5"],
            "unasked_clarification_count": 0,
        },
    )

    saved = db._memory_store["presentation_metrics"][0]
    assert saved["instruction_adherence_score"] == 0.5
    assert saved["missing_element_errors"] == ["slide_6:flowchart"]
    assert saved["wrong_slide_errors"] == ["flowchart_expected_slide_6_found_slide_5"]
    assert saved["unasked_clarification_count"] == 0
    assert saved["instruction_contract"]["slide_count"] == 6


def test_metrics_summary_includes_average_adherence(monkeypatch):
    monkeypatch.setattr(db, "_firestore_client", None)
    monkeypatch.setattr(db, "_firestore_init_attempted", True)
    db._memory_store["presentation_metrics"].clear()
    db._memory_store["presentation_metrics"].extend(
        [
            {"duration_seconds": 10, "error_rate": 0, "slide_count": 4,
             "error_count": 0, "request_count": 10, "instruction_adherence_score": 1.0},
            {"duration_seconds": 20, "error_rate": 0.2, "slide_count": 6,
             "error_count": 2, "request_count": 10, "instruction_adherence_score": 0.5},
        ]
    )

    summary = db.get_metrics_summary()

    assert summary["avg_instruction_adherence_score"] == 0.75
