from app.server import _prepare_generation_prompt


def test_prepare_generation_prompt_wraps_raw_text_in_instruction_contract():
    prepared = _prepare_generation_prompt(
        "Create 6 slides and put a flowchart on slide 6."
    )

    assert prepared["contract"]["slide_count"] == 6
    assert prepared["contract"]["requirements"][0]["kind"] == "flowchart"
    assert "INSTRUCTION CONTRACT" in prepared["agent_prompt"]
    assert "Create 6 slides" in prepared["original_text"]
    assert prepared["mode"] == "silent_contract"
