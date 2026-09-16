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


def test_contract_move_title_not_checkable():
    # "move the title to the left" mentions "title" and has " to " but is a
    # position change, not a retitle — must not fabricate a retitle requirement.
    c = ea.build_edit_contract("move the title to the left")
    assert c["checkable"] is False
    assert c["requirements"] == []


def test_contract_title_color_is_not_a_retitle():
    # Mentions "title" and " to ", but the verb is about colour, not renaming.
    c = ea.build_edit_contract("change the title color on slide 2 to blue")
    kinds = [r["kind"] for r in c["requirements"]]
    assert "retitle" not in kinds


def test_contract_parses_subtitle_retitle_value():
    c = ea.build_edit_contract("change the subtitle of slide 1 to Welcome to Acme")
    (req,) = c["requirements"]
    assert req["kind"] == "retitle"
    assert req["value"] == "Welcome to Acme"


def test_contract_set_title_to_colour_phrase_not_checkable():
    # "set the title to blue text" — value starts with a colour word, so this
    # is a colour instruction wearing retitle clothing, not a real retitle.
    c = ea.build_edit_contract("set the title to blue text")
    assert c["checkable"] is False
    assert c["requirements"] == []


def test_contract_make_title_colour_font_not_a_retitle():
    # "make" is not a rename verb, so this never reaches the retitle branch —
    # covered explicitly per the review finding.
    c = ea.build_edit_contract("make the title red font")
    kinds = [r["kind"] for r in c["requirements"]]
    assert "retitle" not in kinds


def test_contract_parses_delete_chart():
    c = ea.build_edit_contract("delete the chart on slide 3")
    assert c["checkable"]
    (req,) = c["requirements"]
    assert req["kind"] == "delete"
    assert req["slide_index"] == 2
    assert req["target"] == "chart"


def test_contract_parses_remove_image():
    c = ea.build_edit_contract("remove the image on slide 1")
    (req,) = c["requirements"]
    assert req["kind"] == "delete"
    assert req["slide_index"] == 0
    assert req["target"] == "image"


def test_contract_parses_delete_flowchart():
    c = ea.build_edit_contract("delete the flowchart on slide 4")
    (req,) = c["requirements"]
    assert req["kind"] == "delete"
    assert req["slide_index"] == 3
    assert req["target"] == "flowchart"


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


def test_resolve_brand_colors_plain_color_word_skips_web_lookup(monkeypatch):
    # "blue" is a plain colour word — must resolve from the local hex dict,
    # never call the web brand lookup (app.eval._brand_palette).
    called = {"hit": False}

    def _boom(_brand):
        called["hit"] = True
        raise AssertionError("web brand lookup must not be called for a colour word")

    monkeypatch.setattr("app.eval._brand_palette", _boom)
    colors = ea._resolve_brand_colors({"brand": "blue"}, None)
    assert called["hit"] is False
    assert colors == ["#0000FF"]


def test_assess_delete_chart_scores_landed_when_no_image_left():
    state = {"slide_count": 3, "slides": [
        {"slide_id": "s1", "slide_index": 0, "elements": []},
        {"slide_id": "s2", "slide_index": 1, "elements": []},
        {"slide_id": "s3", "slide_index": 2, "elements": []},
    ]}
    a = ea.assess("delete the chart on slide 3", state)
    assert a["checkable"] is True
    assert a["landed"] == 1.0
    assert a["missing"] == []


def test_assess_delete_chart_missing_when_image_still_present():
    state = {"slide_count": 3, "slides": [
        {"slide_id": "s1", "slide_index": 0, "elements": []},
        {"slide_id": "s2", "slide_index": 1, "elements": []},
        {"slide_id": "s3", "slide_index": 2, "elements": [
            {"objectId": "chart1", "type": "image"},
        ]},
    ]}
    a = ea.assess("delete the chart on slide 3", state)
    assert a["landed"] == 0.0
    assert len(a["missing"]) == 1
    assert a["missing"][0]["kind"] == "delete"


def test_assess_delete_flowchart_scores_by_node_prefix():
    state = {"slide_count": 4, "slides": [
        {"slide_id": "s1", "slide_index": 0, "elements": []},
        {"slide_id": "s2", "slide_index": 1, "elements": []},
        {"slide_id": "s3", "slide_index": 2, "elements": []},
        {"slide_id": "s4", "slide_index": 3, "elements": [
            {"objectId": "node_1", "type": "shape"},
        ]},
    ]}
    a = ea.assess("delete the flowchart on slide 4", state)
    assert a["landed"] == 0.0
    state["slides"][3]["elements"] = []
    a2 = ea.assess("delete the flowchart on slide 4", state)
    assert a2["landed"] == 1.0


def test_assess_delete_with_before_ids_ctx_nothing_to_delete():
    # Slide 3 never had a chart — landing "delete the chart" must not be a
    # free 1.0 just because there's no image there now.
    state = {"slide_count": 3, "slides": [
        {"slide_id": "s1", "slide_index": 0, "elements": []},
        {"slide_id": "s2", "slide_index": 1, "elements": []},
        {"slide_id": "s3", "slide_index": 2, "elements": []},
    ]}
    ctx = {"before_ids": {2: set()}}
    a = ea.assess("delete the chart on slide 3", state, ctx=ctx)
    assert a["landed"] == 0.0
    assert len(a["missing"]) == 1
    assert "nothing to delete" in ea.describe_missing(a["missing"][0])


def test_assess_delete_with_before_ids_ctx_landed_when_actually_removed():
    state = {"slide_count": 3, "slides": [
        {"slide_id": "s1", "slide_index": 0, "elements": []},
        {"slide_id": "s2", "slide_index": 1, "elements": []},
        {"slide_id": "s3", "slide_index": 2, "elements": []},  # chart already gone
    ]}
    ctx = {"before_ids": {2: {"chart1"}}}  # a chart existed before the edit
    a = ea.assess("delete the chart on slide 3", state, ctx=ctx)
    assert a["landed"] == 1.0
    assert a["missing"] == []


def test_assess_delete_with_before_ids_ctx_still_present_is_missing():
    state = {"slide_count": 3, "slides": [
        {"slide_id": "s1", "slide_index": 0, "elements": []},
        {"slide_id": "s2", "slide_index": 1, "elements": []},
        {"slide_id": "s3", "slide_index": 2, "elements": [
            {"objectId": "chart1", "type": "image"},
        ]},
    ]}
    ctx = {"before_ids": {2: {"chart1"}}}
    a = ea.assess("delete the chart on slide 3", state, ctx=ctx)
    assert a["landed"] == 0.0


def test_delete_target_ids_identifies_images_and_flowchart_nodes():
    state = {"slide_count": 1, "slides": [
        {"slide_id": "s1", "slide_index": 0, "elements": [
            {"objectId": "chart1", "type": "image"},
            {"objectId": "node_1", "type": "shape"},
            {"objectId": "text1", "type": "shape"},
        ]},
    ]}
    assert ea.delete_target_ids(state, 0, "chart") == {"chart1"}
    assert ea.delete_target_ids(state, 0, "image") == {"chart1"}
    assert ea.delete_target_ids(state, 0, "flowchart") == {"node_1"}


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
