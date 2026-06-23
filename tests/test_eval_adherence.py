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


def test_adherence_and_visual_quality_reflect_messy_layout(monkeypatch):
    # A deck with the right slide count but off-slide, overlapping, rainbow
    # elements must NOT score perfect adherence — layout/colour are part of
    # "accurate", and visual_quality must reflect the mess (not flat 0.5).
    prompt = {
        "id": "messy",
        "name": "Messy Layout",
        "prompt": "Create a 4-slide presentation about remote work.",
        "expected_slides": 4,
        "expected_elements": ["title"],
        "sla_seconds": 30,
    }

    def _el(oid, x, y, w, h, fill):
        return {
            "objectId": oid, "type": "shape",
            "size": {"width": {"magnitude": w}, "height": {"magnitude": h}},
            "transform": {"translateX": x, "translateY": y, "scaleX": 1, "scaleY": 1},
            "fill_color": fill,
        }

    messy_state = {
        "slide_count": 4,
        "slides": [
            {"elements": [
                _el("a", 8_900_000, 4_900_000, 2_000_000, 1_500_000, "#FF0000"),
                _el("b", 8_950_000, 4_950_000, 2_000_000, 1_500_000, "#00FF00"),
                _el("c", 0, 0, 1, 1, "#0000FF"),
                _el("d", 0, 0, 1, 1, "#FFFF00"),
                _el("e", 0, 0, 1, 1, "#FF00FF"),
            ]},
            {"elements": []}, {"elements": []}, {"elements": []},
        ],
    }

    async def fake_generate(_text):
        return {"presentation_id": "p1", "duration_seconds": 10,
                "success_count": 5, "total_requests": 5}

    monkeypatch.setattr("app.slidemakr.get_presentation_state", lambda _pid: messy_state)

    result = asyncio.run(run_single_eval(prompt, fake_generate))

    assert result["scores"]["instruction_adherence"] < 1.0
    assert result["scores"]["visual_quality"] != 0.5  # grounded, not placeholder


def test_branding_request_with_default_template_fails(monkeypatch):
    # The real failure: a "Stripe" deck rendered as a colorless default template.
    # Branding must score 0 and drag adherence down — not score perfect.
    prompt = {
        "id": "brand_fail",
        "name": "Brand Fail",
        "prompt": "Create a 3-slide pitch deck for Stripe using their brand colors.",
        "expected_slides": 3,
        "expected_elements": ["title", "branding"],
        "brand": "Stripe",
        "sla_seconds": 45,
    }
    colorless = {  # default template — no fill_color anywhere
        "slide_count": 3,
        "slides": [{"elements": [{"objectId": "t", "type": "shape"}]} for _ in range(3)],
    }

    async def fake_generate(_text):
        return {"presentation_id": "p1", "duration_seconds": 12,
                "success_count": 6, "total_requests": 6}

    monkeypatch.setattr("app.slidemakr.get_presentation_state", lambda _pid: colorless)
    monkeypatch.setattr("app.eval._brand_palette", lambda _c: ["#635BFF", "#0A2540"])

    result = asyncio.run(run_single_eval(prompt, fake_generate))

    assert result["scores"]["instruction_adherence"] < 0.7
    assert "branding" not in result.get("adherence", {}).get("found", [])  # not falsely credited


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
