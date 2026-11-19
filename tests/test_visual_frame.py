"""Current-frame projections preserve OCR connections without numeric model IDs."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from brain.graph.screen_mapping import ScreenMappingWire
from brain.task_instruction import TaskInstruction
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
        {"ref": f"text:{i}", "text": b["text"], "bbox": b["bbox"]}
        for i, b in enumerate(current["ocr"])
    ]
    assert [c["ref"] for c in projected["control_regions"]] == ["control:0", "control:1"]
    assert all(set(c) == {"ref", "bbox", "contained_text"} for c in projected["control_regions"])
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

    assert projected["text_regions"] == [
        {"ref": "text:0", **inside},
        {"ref": "text:1", **outside},
    ]
    assert projected["control_regions"] == [
        {
            "ref": "control:0",
            "bbox": [0, 0, 100, 50],
            "contained_text": [] if links == [] else [inside],
        }
    ]


def test_uncontained_text_survives_without_control_candidates():
    assert frame_input({"ocr": [{"id": 3, "text": "안내", "bbox": [20, 30, 40, 20]}]}) == {
        "image_size": None,
        "text_regions": [{"ref": "text:0", "text": "안내", "bbox": [20, 30, 40, 20]}],
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

    assert projected["text_regions"] == [
        {"ref": "text:0", "text": "CURRENT", "bbox": [5, 5, 50, 20]}
    ]
    assert projected["control_regions"][0]["contained_text"] == [
        {"text": "CURRENT", "bbox": [5, 5, 50, 20]}
    ]
    assert projected["memory_fields"] == [{"ref": "memory:0", **current["memory_fields"][0]}]
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


@pytest.mark.parametrize(
    ("region_refs", "expected_bbox"),
    [
        (["control:0"], [100, 15, 150, 30]),
        (["text:0", "text:1"], [110, 20, 100, 40]),
        (["memory:0"], [100, 90, 150, 30]),
    ],
    ids=["local-control", "wrapped-ocr-value", "remembered-control"],
)
def test_named_fields_resolve_local_geometry_without_changing_meaning(region_refs, expected_bbox):
    projected = frame_input(
        {
            "image_size": [400, 200],
            "ocr": [
                {"id": 7, "text": "First line", "bbox": [110, 20, 100, 15]},
                {"id": 8, "text": "Second line", "bbox": [115, 45, 80, 15]},
            ],
            "markers": [
                {"id": 42, "source": "omniparser", "bbox": [100, 15, 150, 30]},
                {"id": 43, "source": "omniparser", "bbox": [0, 0, 400, 200]},
            ],
            "memory_fields": [{"name": "Earlier name", "bbox": [100, 90, 150, 30]}],
        }
    )
    before = deepcopy(projected)
    raw = {
        "screen_path": ["Entry", "Details"],
        "fields": [
            {
                "name": "Reference",
                "role": "edit",
                "region_refs": region_refs,
                "source": "reference",
                "value": "Model reading",
                "parent": "Entry",
                "focused": True,
            },
            {"name": "Entry", "role": "window", "region_refs": ["control:1"]},
        ],
        "steps": [
            {
                "action": "fill",
                "target": "Reference",
                "intent": "fill",
                "source": "reference",
                "point": [125.25, 28.75],
            }
        ],
    }

    resolved = ScreenMappingWire.model_validate(raw).resolve(projected)

    field = resolved.fields["Reference"]
    assert field.bbox == expected_bbox
    assert (field.source, field.value, field.parent, field.focused) == (
        "reference",
        "Model reading",
        "Entry",
        True,
    )
    assert resolved.screen_path == raw["screen_path"]
    assert resolved.steps[0].model_dump() == raw["steps"][0]
    assert "region_refs" not in resolved.model_dump()["fields"]["Reference"]
    assert projected == before
    field.bbox[0] = 999
    assert projected == before


@pytest.mark.parametrize("value", [None, ""], ids=["unread", "explicit-blank"])
def test_geometry_resolution_preserves_whether_a_value_was_observed(value):
    field = {"name": "Reference", "role": "edit", "region_refs": ["control:0"]}
    if value is not None:
        field["value"] = value
    projected = frame_input(
        {"markers": [{"id": 3, "source": "omniparser", "bbox": [10, 20, 100, 30]}]}
    )

    resolved = ScreenMappingWire.model_validate({"fields": [field]}).resolve(projected)

    stored = resolved.fields["Reference"]
    assert ("value" in stored.model_fields_set) is (value is not None)
    assert ("value" in stored.model_dump(exclude_unset=True)) is (value is not None)
    if value is not None:
        assert stored.value == ""


def test_unused_large_list_can_only_select_current_geometry_and_cannot_return_a_bbox():
    # Synthetic seq64 boundary: capture height is 2110, while the retired model box reached 2370.
    from brain.graph.screen_decision import ScreenMatch

    projected = frame_input(
        {
            "image_size": [400, 2110],
            "markers": [
                {"id": 7, "source": "omniparser", "bbox": [100, 15, 150, 30]},
                {"id": 8, "source": "omniparser", "bbox": [0, 0, 400, 2110]},
            ],
        }
    )
    raw = {
        "fields": [
            {"name": "Continue", "role": "button", "region_refs": ["control:0"]},
            {"name": "Unused list", "role": "table", "region_refs": ["control:1"]},
        ],
        "steps": [
            {"target": "Continue", "action": "click", "intent": "navigate", "point": [125, 28]}
        ],
    }

    resolved = ScreenMappingWire.model_validate(raw).resolve(projected)

    assert resolved.fields["Unused list"].bbox == [0, 0, 400, 2110]
    assert resolved.fields["Continue"].bbox == [100, 15, 150, 30]
    assert resolved.steps[0].point == [125, 28]
    invented = deepcopy(raw)
    invented["fields"][1]["bbox"] = [0, 0, 400, 2370]
    with pytest.raises(ValidationError, match="bbox"):
        ScreenMappingWire.model_validate(invented)
    with pytest.raises(ValidationError, match="bbox"):
        ScreenMatch.model_validate_json(json.dumps(invented))
    missing = deepcopy(raw)
    missing["fields"][1]["region_refs"] = ["control:1", "control:99"]
    mapping = ScreenMappingWire.model_validate(missing).resolve(projected)
    assert list(mapping.fields) == ["Continue"]
    screen = {}
    plan = mapping.local_payload(screen)
    assert plan["steps"] == [
        {**raw["steps"][0], "source": None, "target": plan["controls"][0]["id"]}
    ]
    assert plan["controls"][0]["visual_description"] == "Continue"
    assert plan["evidence"] == []


@pytest.mark.parametrize(
    ("field_attributes", "dependencies"),
    [
        ({"result_key": "quantity", "value": "30"}, {}),
        ({}, {"completion_evidence": [{"condition_index": 0, "target": "Notice"}]}),
        (
            {},
            {
                "fields": [
                    {"name": "Cell", "role": "edit", "parent": "Notice", "region_refs": ["text:0"]}
                ]
            },
        ),
    ],
    ids=["original-business-reading", "completion-evidence", "observed-parent"],
)
def test_missing_regions_cannot_supply_business_facts_or_referenced_controls(
    field_attributes, dependencies
):
    projected = {"text_regions": [{"ref": "text:0", "bbox": [100, 15, 150, 30]}]}
    field = {
        "name": "Notice",
        "role": "text",
        "region_refs": ["text:0", "text:99"],
        **field_attributes,
    }
    response = {**dependencies, "fields": [field, *dependencies.get("fields", [])]}
    task = TaskInstruction(
        name="Read quantity",
        objective="Collect the displayed quantity",
        completion_conditions=["Quantity is known"],
        result_keys=["quantity"],
    )
    with pytest.raises(ValueError, match="screen_mapping_region_ref_unknown"):
        ScreenMappingWire.model_validate(response).resolve(projected, task=task)
