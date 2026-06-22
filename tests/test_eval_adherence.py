import asyncio

from app.eval import (
    FIRST_SHOT_ADHERENCE_PROMPTS,
    compute_overall_score,
    run_single_eval,
    score_contract_adherence_from_state,
)


def test_first_shot_eval_prompts_include_slide_specific_flowchart_case():
    prompt = next(p for p in FIRST_SHOT_ADHERENCE_PROMPTS if p["id"] == "flowchart_on_slide_6")

    assert prompt["expected_slides"] == 6
    assert prompt["expected_contract"]["requirements"][0]["slide_number"] == 6
    assert prompt["expected_contract"]["requirements"][0]["kind"] == "flowchart"


def test_eval_scores_contract_adherence_from_state():
    contract = {
        "slide_count": 4,
        "requirements": [
            {"slide_number": 2, "kind": "bullets", "quantity": 3, "subtype": ""},
            {"slide_number": 3, "kind": "chart", "subtype": "pie"},
        ],
    }
    state = {
        "slide_count": 4,
        "slides": [
            {"elements": []},
            {"elements": [{"text": "A\nB\nC", "type": "shape", "shapeType": "TEXT_BOX"}]},
            {"elements": [{"type": "image"}]},
            {"elements": []},
        ],
    }

    result = score_contract_adherence_from_state(contract, state)

    assert result["instruction_adherence_score"] == 1.0


def test_overall_score_prioritizes_adherence_and_keeps_speed_weighted():
    fast_wrong = compute_overall_score({
        "instruction_adherence": 0.2,
        "speed": 1.0,
        "visual_quality": 0.8,
        "completeness": 1.0,
        "error_rate": 1.0,
        "content_richness": 0.8,
    })
    slower_right = compute_overall_score({
        "instruction_adherence": 1.0,
        "speed": 0.6,
        "visual_quality": 0.8,
        "completeness": 1.0,
        "error_rate": 1.0,
        "content_richness": 0.8,
    })

    assert slower_right > fast_wrong


def test_run_single_eval_builds_contract_when_prompt_has_no_explicit_contract(monkeypatch):
    # Legacy EVAL_PROMPTS carry expected_slides/expected_elements but no
    # expected_contract. Accuracy must still be scored (built from the prompt
    # text) so the fast+accurate composite is always applied — not the legacy
    # weighting that ignores adherence.
    prompt = {
        "id": "legacy_style",
        "name": "Legacy Style Prompt",
        "prompt": "Create a 4-slide presentation about remote work.",
        "expected_slides": 4,
        "expected_elements": ["title"],
        "sla_seconds": 30,
    }
    state = {"slide_count": 4, "slides": [{"elements": []} for _ in range(4)]}

    async def fake_generate(_text):
        return {
            "presentation_id": "p1",
            "duration_seconds": 10,
            "success_count": 5,
            "total_requests": 5,
        }

    monkeypatch.setattr("app.slidemakr.get_presentation_state", lambda _pid: state)

    result = asyncio.run(run_single_eval(prompt, fake_generate))

    assert "instruction_adherence" in result["scores"]


def test_run_single_eval_includes_instruction_adherence_score(monkeypatch):
    prompt = FIRST_SHOT_ADHERENCE_PROMPTS[0]
    state = {
        "slide_count": 6,
        "slides": [
            {"elements": []},
            {"elements": []},
            {"elements": []},
            {"elements": []},
            {"elements": []},
            {"elements": [{"type": "shape", "shapeType": "RECTANGLE"}]},
        ],
    }

    async def fake_generate(_text):
        return {
            "presentation_id": "p1",
            "duration_seconds": 12,
            "success_count": 10,
            "total_requests": 10,
        }

    monkeypatch.setattr("app.slidemakr.get_presentation_state", lambda _pid: state)

    result = asyncio.run(run_single_eval(prompt, fake_generate))

    assert result["scores"]["instruction_adherence"] == 1.0
