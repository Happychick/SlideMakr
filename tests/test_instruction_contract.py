from app.instruction_contract import (
    build_instruction_contract,
    build_contract_prompt,
    score_instruction_adherence,
)


def test_contract_extracts_slide_count_and_flowchart_target():
    contract = build_instruction_contract(
        "Create 6 slides and put a flowchart on slide 6 about the onboarding process."
    )

    assert contract["slide_count"] == 6
    assert contract["requirements"][0]["slide_number"] == 6
    assert contract["requirements"][0]["kind"] == "flowchart"
    assert "onboarding process" in contract["requirements"][0]["description"].lower()


def test_contract_extracts_messy_voice_slide_specific_requirements():
    contract = build_instruction_contract(
        "um can you like make exactly 4 slides for our investor update. "
        "slide 2 should have three bullets, slide 3 should have a pie chart, "
        "and slide 4 should be a closing slide. Make it McKinsey style."
    )

    assert contract["slide_count"] == 4
    assert contract["style"] == "McKinsey style"
    assert {
        (req["slide_number"], req["kind"], req.get("quantity"), req.get("subtype"))
        for req in contract["requirements"]
    } >= {
        (2, "bullets", 3, ""),
        (3, "chart", None, "pie"),
        (4, "closing", None, ""),
    }


def test_contract_prompt_makes_adherence_and_speed_explicit():
    contract = build_instruction_contract("Create 6 slides and put a flowchart on slide 6.")
    prompt = build_contract_prompt("Create 6 slides and put a flowchart on slide 6.", contract)

    assert "INSTRUCTION CONTRACT" in prompt
    assert "slide 6" in prompt.lower()
    assert "flowchart" in prompt.lower()
    assert "fast" in prompt.lower()
    assert "Do not ask clarifying questions" in prompt


def test_adherence_scores_correct_slide_requirement_as_perfect():
    contract = build_instruction_contract("Create 6 slides and put a flowchart on slide 6.")
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

    result = score_instruction_adherence(contract, state)

    assert result["instruction_adherence_score"] == 1.0
    assert result["missing_element_errors"] == []
    assert result["wrong_slide_errors"] == []


def test_adherence_reports_wrong_slide_and_missing_target():
    contract = build_instruction_contract("Create 6 slides and put a flowchart on slide 6.")
    state = {
        "slide_count": 6,
        "slides": [
            {"elements": []},
            {"elements": []},
            {"elements": []},
            {"elements": []},
            {"elements": [{"type": "shape", "shapeType": "RECTANGLE"}]},
            {"elements": []},
        ],
    }

    result = score_instruction_adherence(contract, state)

    assert result["instruction_adherence_score"] < 1.0
    assert result["missing_element_errors"] == ["slide_6:flowchart"]
    assert result["wrong_slide_errors"] == ["flowchart_expected_slide_6_found_slide_5"]
