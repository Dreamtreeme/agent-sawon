"""Current-frame projections preserve OCR connections without numeric model IDs."""

from copy import deepcopy

import pytest

from brain.visual.frame import frame_input


@pytest.mark.parametrize("reverse_order", [False, True], ids=["original-order", "reversed-order"])
@pytest.mark.parametrize("renumber", [False, True], ids=["original-ids", "renumbered-ids"])
def test_control_text_connections_survive_order_and_id_changes(reverse_order, renumber):
    current = {
        "image_size": [400, 950],
        "ocr": [
            {"id": 21, "text": "저장", "bbox": [59, 881, 54, 35]},
            {"id": 22, "text": "닫기", "bbox": [179, 880, 54, 37]},
        ],
        "markers": [
            {
                "id": 27,
                "source": "omniparser",
                "bbox": [149, 873, 113, 51],
                "contained_ocr_ids": [22],
            },
            {
                "id": 28,
                "source": "omniparser",
                "bbox": [32, 875, 112, 49],
                "contained_ocr_ids": [21],
            },
        ],
    }
    if reverse_order:
        current["ocr"].reverse()
        current["markers"].reverse()
    if renumber:
        for block in current["ocr"]:
            block["id"] += 100
        for marker in current["markers"]:
            marker["id"] += 200
            marker["contained_ocr_ids"] = [i + 100 for i in marker["contained_ocr_ids"]]
    before = deepcopy(current)

    projected = frame_input(current)

    assert projected["image_size"] == [400, 950]
    assert {tuple(c["bbox"]): c["contained_text"] for c in projected["control_regions"]} == {
        (149, 873, 113, 51): [{"text": "닫기", "bbox": [179, 880, 54, 37]}],
        (32, 875, 112, 49): [{"text": "저장", "bbox": [59, 881, 54, 35]}],
    }
    assert projected["text_regions"] == [
        {"text": b["text"], "bbox": b["bbox"]} for b in current["ocr"]
    ]
    assert all(set(c) == {"bbox", "contained_text"} for c in projected["control_regions"])
    projected["control_regions"][0]["bbox"][0] = 999
    projected["text_regions"][0]["bbox"][0] = 999
    assert current == before


@pytest.mark.parametrize(
    "links", [None, [], [7]], ids=["legacy-geometry", "explicit-empty", "linked"]
)
def test_existing_connections_take_precedence_over_geometry(links):
    marker = {"id": 1, "source": "omniparser", "bbox": [0, 0, 100, 50]}
    if links is not None:
        marker["contained_ocr_ids"] = links
    inside = {"text": "현재 값", "bbox": [10, 10, 30, 20]}
    outside = {"text": "바깥 설명", "bbox": [200, 10, 60, 20]}

    projected = frame_input(
        {
            "ocr": [{"id": 7, **inside}, {"id": 8, **outside}],
            "markers": [marker, {"id": 2, "source": "ocr", "bbox": inside["bbox"]}],
        }
    )

    assert projected["text_regions"] == [inside, outside]
    assert projected["control_regions"] == [
        {"bbox": [0, 0, 100, 50], "contained_text": [] if links == [] else [inside]}
    ]


def test_uncontained_text_survives_without_control_candidates():
    assert frame_input({"ocr": [{"id": 3, "text": "안내", "bbox": [20, 30, 40, 20]}]}) == {
        "image_size": None,
        "text_regions": [{"text": "안내", "bbox": [20, 30, 40, 20]}],
        "control_regions": [],
    }


def test_retrieved_memory_and_correction_stay_separate_from_current_observations():
    current = {
        "ocr": [{"id": 1, "text": "CURRENT", "bbox": [5, 5, 50, 20]}],
        "markers": [
            {"id": 2, "source": "omniparser", "bbox": [0, 0, 100, 30], "contained_ocr_ids": [1]}
        ],
        "memory_fields": [{"name": "Reference", "bbox": [0, 0, 100, 30], "value": "OLD"}],
        "screen_path": ["Entry"],
        "selected_fields": {"Reference": {"value": "UNOBSERVED"}},
        "retrieved_procedure": [
            {
                "name": "Review",
                "steps": [
                    {"action": "key", "keys": "pagedown", "source": "unused"},
                    {"action": "key", "keys": "pageup", "source": None},
                    {"action": "key", "keys": "enter", "source": "unused"},
                    {"action": "fill", "target": "Reference", "source": "reference", "keys": None},
                ],
            }
        ],
        "correction": {"reason": "retry", "bbox": [0, 0, 100, 30]},
    }
    before = deepcopy(current)

    projected = frame_input(current)

    assert projected["text_regions"] == [{"text": "CURRENT", "bbox": [5, 5, 50, 20]}]
    assert projected["control_regions"][0]["contained_text"] == projected["text_regions"]
    assert projected["memory_fields"] == current["memory_fields"]
    assert projected["memory_screen_path"] == ["Entry"]
    assert projected["retrieved_procedure"] == [
        {
            "name": "Review",
            "steps": [
                {"action": "scroll", "direction": "down"},
                {"action": "scroll", "direction": "up"},
                {"action": "key", "keys": "enter"},
                {"action": "fill", "target": "Reference", "source": "reference"},
            ],
        }
    ]
    assert projected["correction"] == current["correction"]
    assert "selected_fields" not in projected
    projected["memory_fields"][0]["value"] = "changed output"
    projected["correction"]["bbox"][0] = 999
    assert current == before
