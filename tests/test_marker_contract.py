"""Visible label IDs cross the model boundary without guessing OCR namespaces."""

import pytest
from test_brain import record as record

from brain.visual.marker_contract import (
    MarkerReferenceError,
    local_response,
    model_controls,
    model_screen,
)
from brain.visual.scene import SemanticDetection


def screen():
    # ID 4 exists in BOTH spaces but denotes different text; never infer a namespace.
    return {
        "ocr": [{"id": 4, "text": "Account"}, {"id": 5, "text": "Password"}],
        "markers": [
            {"id": 7, "source": "omniparser", "text_ids": [], "contained_ocr_ids": []},
            {"id": 8, "source": "ocr", "text_ids": [4], "contained_ocr_ids": [4]},
            {"id": 4, "source": "ocr", "text_ids": [5], "contained_ocr_ids": [5]},
        ],
    }


def test_image_and_text_catalog_share_visible_ids_and_round_trip():
    current = screen()
    public = model_screen(current)
    assert public["ocr"] == [{"id": 8, "text": "Account"}, {"id": 4, "text": "Password"}]
    assert public["markers"][1]["text_ids"] == [8]
    assert public["markers"][2]["contained_ocr_ids"] == [4]
    assert public["markers"][0]["target_kind"] == "detector_surface"
    assert public["markers"][0]["allowed_actions"] == [
        "fill",
        "click",
        "double_click",
        "key",
    ]
    assert public["markers"][1]["target_kind"] == "ocr_click_proxy"
    assert public["markers"][1]["allowed_actions"] == ["click", "double_click"]
    external = SemanticDetection(
        controls=[
            {
                "id": 7,
                "role": "edit",
                "label_ids": [4],
                "confidence": 1,
            }
        ]
    )
    internal = local_response(external, current)
    assert internal.controls[0].label_ids == [5]  # visible 4, not old OCR 4
    assert model_controls(internal.controls, current)[0]["label_ids"] == [4]
    assert current["ocr"][0]["id"] == 4  # source evidence was not mutated


@pytest.mark.parametrize("field", ["label_ids", "text_ids"])
def test_unknown_and_nontext_references_report_exact_field(field):
    response = SemanticDetection(
        controls=[
            {
                "id": 7,
                "role": "edit",
                field: [7, 99],
                "confidence": 1,
            }
        ]
    )
    with pytest.raises(MarkerReferenceError) as caught:
        local_response(response, screen())
    assert caught.value.issues == [
        {
            "marker_id": 7,
            "field": field,
            "rejected_ids": [7, 99],
            "allowed_text_marker_ids": [4, 8],
        }
    ]
