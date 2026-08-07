"""
Tests for the shared edit assessment (app/edit_assess): the ONE judge used both
live (edit_runner controller) and in the offline eval.

Covers the instruction→contract parser and the assess() verdict. Pure Python —
no Google API, no network (recolor uses an explicit ctx palette).
"""

from __future__ import annotations

from app import edit_assess as ea


# ---------------------------------------------------------------------------
# build_edit_contract
# ---------------------------------------------------------------------------

def test_contract_parses_retitle():
    c = ea.build_edit_contract("change the title of slide 1 to Q4 Board Review")
    assert c["checkable"]
    (req,) = c["requirements"]
    assert req["kind"] == "retitle"
    assert req["slide_index"] == 0          # slide 1 → index 0
    assert req["value"] == "Q4 Board Review"


def test_contract_parses_recolor_with_slide_and_target():
    c = ea.build_edit_contract("recolor the bullets on slide 2 to Stripe purple")
    (req,) = c["requirements"]
    assert req["kind"] == "recolor"
    assert req["slide_index"] == 1
    assert "stripe purple" in req["brand"].lower()


def test_contract_parses_vertical_flowchart():
    c = ea.build_edit_contract(
        "add a vertical flowchart to slide 3 showing plan then build then ship"
    )
    (req,) = c["requirements"]
    assert req["kind"] == "flowchart"
    assert req["slide_index"] == 2
    assert req["vertical"] is True


def test_contract_unparseable_is_not_checkable():
    c = ea.build_edit_contract("make it look nicer please")
    assert c["checkable"] is False
    assert c["requirements"] == []


# ---------------------------------------------------------------------------
# assess
# ---------------------------------------------------------------------------

def _state_with_title(idx, title):
    slides = [{"slide_id": f"s{i}", "slide_index": i, "elements": []} for i in range(idx + 1)]
    slides[idx]["elements"] = [{"objectId": "t", "type": "shape",
                                "placeholder": "TITLE", "text": title,
                                "transform": {"translateY": 100_000}}]
    return {"slide_count": len(slides), "slides": slides}


def test_assess_landed_when_title_matches():
    instr = "change the title of slide 1 to Q4 Board Review"
    a = ea.assess(instr, _state_with_title(0, "Q4 Board Review"))
    assert a["checkable"] is True
    assert a["landed"] == 1.0
    assert a["missing"] == []


def test_assess_reports_missing_when_title_wrong():
    instr = "change the title of slide 1 to Q4 Board Review"
    a = ea.assess(instr, _state_with_title(0, "Original Title"))
    assert a["landed"] == 0.0
    assert len(a["missing"]) == 1
    assert a["missing"][0]["kind"] == "retitle"


def test_assess_not_checkable_passthrough():
    a = ea.assess("make it look nicer", {"slide_count": 1, "slides": [{"elements": []}]})
    assert a["checkable"] is False
    assert a["landed"] is None
    assert a["missing"] == []


def test_describe_observed_reports_current_title():
    # Read-after-write signal: the corrective turn should see the ACTUAL title.
    state = _state_with_title(0, "Original Title")
    req = ea.build_edit_contract("change the title of slide 1 to Q4 Board Review")["requirements"][0]
    observed = ea.describe_observed(req, state)
    assert "Original Title" in observed


def test_assess_recolor_uses_ctx_palette():
    # slide 2 (index 1) painted Stripe purple; ctx supplies the target palette.
    slides = [
        {"slide_id": "s1", "slide_index": 0, "elements": []},
        {"slide_id": "s2", "slide_index": 1,
         "elements": [{"objectId": "b", "type": "shape", "text_color": "#635BFF"}]},
    ]
    state = {"slide_count": 2, "slides": slides}
    a = ea.assess("recolor the bullets on slide 2 to Stripe purple", state,
                  ctx={"brand_colors": ["#635BFF"]})
    assert a["checkable"] is True
    assert a["landed"] == 1.0
