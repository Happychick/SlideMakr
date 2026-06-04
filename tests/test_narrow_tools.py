"""
Unit tests for the 28 narrow Slides-editing tools.

Each test invokes a narrow tool in Mode A (commit-buffer), asserts the buffered
request has the expected top-level key (Slides API request type), and asserts
the dumped shape validates against `validate_typed_requests` — i.e. it would
actually be accepted by the Google Slides batchUpdate API after the existing
defence-in-depth pass.

These tests are pure Python — no Google API, no network, no auth. They run
anywhere pytest runs.
"""

from __future__ import annotations

import os

# Force Mode A (commit-buffer) BEFORE importing narrow_tools so BATCH_MODE
# reads the right value.
os.environ["SLIDEMAKR_BATCH_MODE"] = "commit"

import pytest  # noqa: E402

from app import narrow_tools  # noqa: E402
from app import slide_batch  # noqa: E402
from app.slides_schema import REQUEST_MODELS, validate_typed_requests  # noqa: E402


PID = "pres_test_deck"


@pytest.fixture(autouse=True)
def _clear_buffer():
    """Ensure each test starts with an empty buffer."""
    slide_batch.clear()
    yield
    slide_batch.clear()


def _drain_and_validate() -> list:
    """Pull buffered requests and run them through the Pydantic validator.

    Returns the validated list so tests can assert on shape.
    """
    reqs = slide_batch.drain(PID)
    # Any ValueError here means the narrow tool produced an invalid request
    # shape — the test should fail.
    return validate_typed_requests(reqs)


def _assert_single_request_of_type(req_type: str) -> dict:
    """Drain the buffer and assert exactly one request of the given type."""
    reqs = _drain_and_validate()
    assert len(reqs) == 1, f"expected 1 request of {req_type}, got {len(reqs)}: {reqs}"
    assert req_type in reqs[0], f"expected top-level key {req_type!r}, got {list(reqs[0].keys())}"
    return reqs[0][req_type]


# ---------------------------------------------------------------------------
# Slide-level
# ---------------------------------------------------------------------------


def test_add_slide_minimal():
    narrow_tools.add_slide(PID, insertion_index=1, layout="TITLE_AND_BODY",
                           title_id="t1", body_id="b1", object_id="slide_x")
    body = _assert_single_request_of_type("createSlide")
    assert body["objectId"] == "slide_x"
    assert body["insertionIndex"] == 1
    assert body["slideLayoutReference"]["predefinedLayout"] == "TITLE_AND_BODY"
    mappings = body["placeholderIdMappings"]
    assert any(m["objectId"] == "t1" and m["layoutPlaceholder"]["type"] == "TITLE" for m in mappings)
    assert any(m["objectId"] == "b1" and m["layoutPlaceholder"]["type"] == "BODY" for m in mappings)


def test_reorder_slides():
    narrow_tools.reorder_slides(PID, ["s1", "s2"], insertion_index=0)
    body = _assert_single_request_of_type("updateSlidesPosition")
    assert body["slideObjectIds"] == ["s1", "s2"]
    assert body["insertionIndex"] == 0


def test_update_slide_flags():
    narrow_tools.update_slide_flags(PID, "slide_1", is_skipped=True)
    body = _assert_single_request_of_type("updateSlideProperties")
    assert body["slideProperties"]["isSkipped"] is True
    assert "isSkipped" in body["fields"]


def test_set_slide_background():
    narrow_tools.set_slide_background(PID, "slide_1", "#0F172A")
    body = _assert_single_request_of_type("updatePageProperties")
    rgb = body["pageProperties"]["pageBackgroundFill"]["solidFill"]["color"]["rgbColor"]
    assert 0.05 < rgb["red"] < 0.07
    assert 0.08 < rgb["green"] < 0.1
    assert 0.16 < rgb["blue"] < 0.17


# ---------------------------------------------------------------------------
# Element creation
# ---------------------------------------------------------------------------


def test_add_shape():
    narrow_tools.add_shape(PID, "slide_1", "ROUND_RECTANGLE", 100, 200, 3000, 800, "shape_a")
    body = _assert_single_request_of_type("createShape")
    assert body["objectId"] == "shape_a"
    assert body["shapeType"] == "ROUND_RECTANGLE"
    assert body["elementProperties"]["pageObjectId"] == "slide_1"
    assert body["elementProperties"]["transform"]["translateX"] == 100
    assert body["elementProperties"]["size"]["width"]["magnitude"] == 3000


def test_add_text_box_emits_createShape_and_insertText():
    narrow_tools.add_text_box(PID, "slide_1", "Hello", 0, 0, 4_000_000, 800_000, "tb1")
    reqs = _drain_and_validate()
    assert len(reqs) == 2
    assert "createShape" in reqs[0]
    assert reqs[0]["createShape"]["shapeType"] == "TEXT_BOX"
    assert "insertText" in reqs[1]
    assert reqs[1]["insertText"]["text"] == "Hello"
    assert reqs[1]["insertText"]["objectId"] == "tb1"


def test_add_image():
    narrow_tools.add_image(PID, "slide_1", "https://example.com/x.png", 0, 0, 100, 100, "img1")
    body = _assert_single_request_of_type("createImage")
    assert body["url"] == "https://example.com/x.png"
    assert body["objectId"] == "img1"


def test_add_table():
    narrow_tools.add_table(PID, "slide_1", 3, 4, 0, 0, 5_000_000, 3_000_000, "tbl1")
    body = _assert_single_request_of_type("createTable")
    assert body["rows"] == 3
    assert body["columns"] == 4
    assert body["objectId"] == "tbl1"


def test_add_line():
    narrow_tools.add_line(PID, "slide_1", 100, 200, 3000, 100, "line1", "STRAIGHT")
    body = _assert_single_request_of_type("createLine")
    assert body["lineCategory"] == "STRAIGHT"
    assert body["objectId"] == "line1"


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def test_insert_text():
    narrow_tools.insert_text(PID, "body_1", "hi", insertion_index=5)
    body = _assert_single_request_of_type("insertText")
    assert body["text"] == "hi"
    assert body["insertionIndex"] == 5
    assert body["objectId"] == "body_1"


def test_insert_text_in_table_cell():
    narrow_tools.insert_text(PID, "tbl_1", "cell", cell_row=1, cell_col=2)
    body = _assert_single_request_of_type("insertText")
    assert body["cellLocation"] == {"rowIndex": 1, "columnIndex": 2}


def test_delete_text_default_all():
    narrow_tools.delete_text(PID, "body_1")
    body = _assert_single_request_of_type("deleteText")
    assert body["textRange"]["type"] == "ALL"


def test_update_text_compound():
    narrow_tools.update_text(PID, "title_1", "New Title")
    reqs = _drain_and_validate()
    assert len(reqs) == 2
    assert "deleteText" in reqs[0]
    assert reqs[0]["deleteText"]["textRange"]["type"] == "ALL"
    assert "insertText" in reqs[1]
    assert reqs[1]["insertText"]["text"] == "New Title"


def test_replace_all_text():
    narrow_tools.replace_all_text(PID, "foo", "bar", match_case=True, slide_ids="s1,s2")
    body = _assert_single_request_of_type("replaceAllText")
    assert body["containsText"]["text"] == "foo"
    assert body["containsText"]["matchCase"] is True
    assert body["replaceText"] == "bar"
    assert body["pageObjectIds"] == ["s1", "s2"]


def test_update_text_style_bold_color_size():
    narrow_tools.update_text_style(PID, "title_1", bold=True, color_hex="#FF0000", size_pt=24)
    body = _assert_single_request_of_type("updateTextStyle")
    assert body["style"]["bold"] is True
    assert body["style"]["fontSize"] == {"magnitude": 24, "unit": "PT"}
    fg = body["style"]["foregroundColor"]["opaqueColor"]["rgbColor"]
    assert fg["red"] == 1.0 and fg["green"] == 0.0 and fg["blue"] == 0.0
    assert "bold" in body["fields"]
    assert "fontSize" in body["fields"]
    assert "foregroundColor" in body["fields"]


def test_update_text_style_requires_some_field():
    r = narrow_tools.update_text_style(PID, "x")
    assert r["status"] == "error"


def test_set_paragraph_style():
    narrow_tools.set_paragraph_style(PID, "body_1", alignment="CENTER", line_spacing=1.5)
    body = _assert_single_request_of_type("updateParagraphStyle")
    assert body["style"]["alignment"] == "CENTER"
    assert body["style"]["lineSpacing"] == 1.5


def test_add_bullets():
    narrow_tools.add_bullets(PID, "body_1", preset="BULLET_STAR_CIRCLE_SQUARE")
    body = _assert_single_request_of_type("createParagraphBullets")
    assert body["bulletPreset"] == "BULLET_STAR_CIRCLE_SQUARE"
    assert body["textRange"]["type"] == "ALL"


# ---------------------------------------------------------------------------
# Transforms & styling
# ---------------------------------------------------------------------------


def test_move_element():
    narrow_tools.move_element(PID, "el_1", 500_000, 600_000)
    body = _assert_single_request_of_type("updatePageElementTransform")
    assert body["applyMode"] == "ABSOLUTE"
    assert body["transform"]["translateX"] == 500_000
    assert body["transform"]["translateY"] == 600_000
    assert body["transform"]["scaleX"] == 1.0


def test_resize_element():
    narrow_tools.resize_element(PID, "el_1", scale_x=2.0, scale_y=2.0, x=100, y=200)
    body = _assert_single_request_of_type("updatePageElementTransform")
    assert body["transform"]["scaleX"] == 2.0
    assert body["transform"]["scaleY"] == 2.0
    assert body["transform"]["translateX"] == 100


def test_delete_element():
    narrow_tools.delete_element(PID, "el_1")
    body = _assert_single_request_of_type("deleteObject")
    assert body["objectId"] == "el_1"


def test_duplicate_element():
    narrow_tools.duplicate_element(PID, "el_1")
    body = _assert_single_request_of_type("duplicateObject")
    assert body["objectId"] == "el_1"


def test_set_element_color_both():
    narrow_tools.set_element_color(PID, "shape_1", fill_color_hex="#00FF00",
                                    outline_color_hex="#000000", outline_weight_pt=2.5)
    body = _assert_single_request_of_type("updateShapeProperties")
    props = body["shapeProperties"]
    fill = props["shapeBackgroundFill"]["solidFill"]["color"]["rgbColor"]
    assert fill["green"] == 1.0
    out = props["outline"]
    assert out["weight"] == {"magnitude": 2.5, "unit": "PT"}


def test_set_element_color_requires_some_field():
    r = narrow_tools.set_element_color(PID, "shape_1")
    assert r["status"] == "error"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def test_insert_table_row():
    narrow_tools.insert_table_row(PID, "tbl1", row=1, column=0, below=True, count=2)
    body = _assert_single_request_of_type("insertTableRows")
    assert body["tableObjectId"] == "tbl1"
    assert body["insertBelow"] is True
    assert body["number"] == 2


def test_insert_table_column():
    narrow_tools.insert_table_column(PID, "tbl1", row=0, column=0, right=False, count=1)
    body = _assert_single_request_of_type("insertTableColumns")
    assert body["insertRight"] is False


def test_delete_table_row():
    narrow_tools.delete_table_row(PID, "tbl1", row=2, column=0)
    body = _assert_single_request_of_type("deleteTableRow")
    assert body["cellLocation"] == {"rowIndex": 2, "columnIndex": 0}


def test_delete_table_column():
    narrow_tools.delete_table_column(PID, "tbl1", row=0, column=3)
    body = _assert_single_request_of_type("deleteTableColumn")
    assert body["cellLocation"] == {"rowIndex": 0, "columnIndex": 3}


def test_set_cell_background():
    narrow_tools.set_cell_background(PID, "tbl1", 0, 0, 1, 2, "#ABCDEF")
    body = _assert_single_request_of_type("updateTableCellProperties")
    assert body["tableRange"]["location"] == {"rowIndex": 0, "columnIndex": 0}
    assert body["tableRange"]["rowSpan"] == 1
    assert body["tableRange"]["columnSpan"] == 2


def test_merge_cells():
    narrow_tools.merge_cells(PID, "tbl1", 0, 0, 2, 3)
    body = _assert_single_request_of_type("mergeTableCells")
    assert body["tableRange"]["rowSpan"] == 2
    assert body["tableRange"]["columnSpan"] == 3


def test_unmerge_cells():
    narrow_tools.unmerge_cells(PID, "tbl1", 0, 0, 2, 3)
    body = _assert_single_request_of_type("unmergeTableCells")
    assert body["tableRange"]["rowSpan"] == 2


# ---------------------------------------------------------------------------
# Lines
# ---------------------------------------------------------------------------


def test_set_line_style():
    narrow_tools.set_line_style(PID, "ln1", weight_pt=3, dash_style="DASH", color_hex="#FFFFFF")
    body = _assert_single_request_of_type("updateLineProperties")
    assert body["lineProperties"]["weight"] == {"magnitude": 3.0, "unit": "PT"}
    assert body["lineProperties"]["dashStyle"] == "DASH"


# ---------------------------------------------------------------------------
# Full coverage guarantee — every wrapper in REQUEST_MODELS has a test above
# ---------------------------------------------------------------------------


def test_every_wrapper_has_a_narrow_tool():
    """Regression guard: every Slides API request type we model must be
    emittable through at least one narrow tool. We drive every tool with a
    representative call and check the set of top-level keys equals the
    set of types in REQUEST_MODELS (minus 'replaceAllShapesWithImage' etc.
    which aren't modeled).
    """
    slide_batch.clear()
    # Drive every narrow tool. Compounds emit multiple request types.
    narrow_tools.add_slide(PID, 0, "TITLE_AND_BODY", "t", "b", "s")
    narrow_tools.reorder_slides(PID, ["s"], 0)
    narrow_tools.update_slide_flags(PID, "s", True)
    narrow_tools.set_slide_background(PID, "s", "#000000")
    narrow_tools.add_shape(PID, "s", "RECTANGLE", 0, 0, 100, 100, "sh")
    narrow_tools.add_text_box(PID, "s", "x", 0, 0, 100, 100, "tb")
    narrow_tools.add_image(PID, "s", "https://x/x.png", 0, 0, 100, 100, "im")
    narrow_tools.add_table(PID, "s", 2, 2, 0, 0, 100, 100, "tbl")
    narrow_tools.add_line(PID, "s", 0, 0, 100, 100, "ln")
    narrow_tools.insert_text(PID, "tb", "hi")
    narrow_tools.delete_text(PID, "tb")
    narrow_tools.update_text(PID, "tb", "new")
    narrow_tools.replace_all_text(PID, "a", "b")
    narrow_tools.update_text_style(PID, "tb", bold=True)
    narrow_tools.set_paragraph_style(PID, "tb", alignment="CENTER")
    narrow_tools.add_bullets(PID, "tb")
    narrow_tools.move_element(PID, "sh", 0, 0)
    narrow_tools.resize_element(PID, "sh", 1.0, 1.0, 0, 0)
    narrow_tools.delete_element(PID, "sh")
    narrow_tools.duplicate_element(PID, "sh")
    narrow_tools.set_element_color(PID, "sh", fill_color_hex="#ff00ff")
    narrow_tools.insert_table_row(PID, "tbl", 0, 0)
    narrow_tools.insert_table_column(PID, "tbl", 0, 0)
    narrow_tools.delete_table_row(PID, "tbl", 1)
    narrow_tools.delete_table_column(PID, "tbl", 0, 1)
    narrow_tools.set_cell_background(PID, "tbl", 0, 0, 1, 1, "#abcdef")
    narrow_tools.merge_cells(PID, "tbl", 0, 0, 1, 1)
    narrow_tools.unmerge_cells(PID, "tbl", 0, 0, 1, 1)
    narrow_tools.set_line_style(PID, "ln", weight_pt=1)

    reqs = _drain_and_validate()
    emitted_types = {next(iter(r.keys())) for r in reqs}

    # All 26 wrapper types must have been exercised
    expected = set(REQUEST_MODELS.keys())
    missing = expected - emitted_types
    assert not missing, f"Wrapper types with no covering narrow tool: {missing}"
    # And no unknown type leaked through
    unknown = emitted_types - expected
    assert not unknown, f"Narrow tools emitted unknown request types: {unknown}"
    # Hallucination rate is structurally 0 in this test
    assert len(reqs) > 0
