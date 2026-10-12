import pytest
from pydantic import ValidationError

from brain.cache.methods import (
    LearnedMethod,
    MethodLibrary,
    MethodSlot,
    compile_method_candidate,
    method_completion_from_match,
    method_from_match,
)
from brain.graph.matching import ScreenMatch


def scene(*, offset=0, old_value="Old customer", ids=0):
    return {
        "ocr": [
            {"id": 1 + ids, "text": "Customer", "bbox": [10, 30 + offset, 80, 20]},
            {"id": 2 + ids, "text": old_value, "bbox": [110, 30 + offset, 120, 20]},
            {"id": 3 + ids, "text": "Search", "bbox": [400, 30 + offset, 60, 20]},
            {"id": 4 + ids, "text": "Search panel", "bbox": [10, 5, 120, 20]},
        ],
        "markers": [
            {
                "id": 10 + ids,
                "source": "omniparser",
                "bbox": [100, 25 + offset, 200, 30],
                "text_ids": [],
            },
            {
                "id": 11 + ids,
                "source": "ocr",
                "bbox": [400, 30 + offset, 60, 20],
                "text_ids": [3 + ids],
            },
        ],
    }


def learned():
    response = {
        "screen_kind": "query",
        "controls": [
            {"id": 10, "role": "edit", "label_ids": [1], "text_ids": [2]},
            {"id": 11, "role": "button", "label_ids": [], "text_ids": [3]},
        ],
        "steps": [
            {"target": 10, "action": "fill", "intent": "query_input", "source": "party"},
            {"target": 11, "action": "click", "intent": "search", "point": [415, 40]},
        ],
        "expected_result": "The supplied customer's search results are shown.",
    }
    method = method_from_match(
        scene(),
        response,
        method_id="find-party",
        purpose="Find the requested business party",
        task_objective="Prepare a delivery",
        slots=[MethodSlot(name="requested_party", description="Party to find", source_key="party")],
        context_anchors=["Search panel"],
        source_transitions=["source-run:4"],
    )
    assert method is not None
    return method


def test_learned_method_rebinds_new_values_keys_and_current_geometry(tmp_path):
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    library.upsert(learned())
    # A new instance must actually consume the saved method rather than retain a response.
    restored = MethodLibrary(library.path, enabled=True)
    bound = restored.bind_with_context(
        "find-party",
        {"requested_party": "supplier"},
        scene(offset=100, old_value="", ids=100),
        {"supplier": "New supplier"},
    )
    assert bound is not None
    response = ScreenMatch.model_validate(bound.response)
    assert response.steps[0].source == "supplier"
    assert [step.target for step in response.steps] == [110, 111]
    assert response.steps[1].point == [415, 140]
    assert response.controls[0].label_ids == [101]
    assert response.evidence == []
    text = library.path.read_text(encoding="utf-8")
    assert "Old customer" not in text
    assert all(target.bbox is None for group in learned().action_groups for target in group.targets)
    assert restored.catalog("Prepare a delivery")[0]["id"] == "find-party"
    assert restored.catalog("Unrelated task") == []


def test_library_rejects_same_method_id_from_another_task_contract(tmp_path):
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    first = learned()
    library.upsert(first)
    other_task = first.model_copy(
        update={
            "task_contract_sha256": "f" * 64,
        }
    )

    with pytest.raises(ValueError, match="method_id_task_scope_collision"):
        library.upsert(other_task)

    assert library.methods() == [first]


def test_model_point_method_scales_with_screen_and_requires_stable_context(tmp_path):
    screen = {
        "image_size": [800, 600],
        "ocr": [{"id": 1, "text": "Shipment editor", "bbox": [20, 20, 160, 20]}],
        "markers": [],
    }
    response = {
        "screen_kind": "editor",
        "controls": [
            {
                "id": 90,
                "role": "edit",
                "label_ids": [],
                "text_ids": [],
                "confidence": 1,
                "confidence_policy": "argmax",
            }
        ],
        "steps": [
            {
                "target": 90,
                "action": "fill",
                "intent": "fill",
                "source": "reference",
                "point": [200, 300],
            }
        ],
        "operation": "act",
        "expected_result": "The reference is entered.",
    }
    method = method_from_match(
        screen,
        response,
        method_id="point-entry",
        purpose="Enter a reference",
        context_anchors=["Shipment editor"],
    )
    assert method is not None
    target = method.action_groups[0].targets[0]
    assert target.screen_relative_point == (0.25, 0.5)
    compiled = compile_method_candidate(method, {"reference": "REF-001"})
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    library.upsert(compiled)

    current = {
        "image_size": [1600, 900],
        "screen_kind": "editor",
        "ocr": [{"id": 11, "text": "Shipment editor", "bbox": [40, 30, 320, 30]}],
        "markers": [],
    }
    bound = library.bind("point-entry", {}, current, {"reference": "REF-002"})
    assert bound is not None
    assert bound["steps"] == [
        {
            "target": bound["controls"][0]["id"],
            "action": "fill",
            "intent": "fill",
            "source": "reference",
            "keys": None,
            "point": [400.0, 450.0],
        }
    ]
    assert bound["controls"][0]["confidence_policy"] == "argmax"
    current["ocr"][0]["text"] = "Unrelated window"
    assert library.bind("point-entry", {}, current, {"reference": "REF-002"}) is None


def test_learned_fill_targets_rebind_current_values_as_visible_evidence(tmp_path):
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    library.upsert(learned())
    current = scene(offset=100, old_value="New supplier", ids=100)

    controls, evidence = library.bind_visible_value_evidence(
        "find-party",
        {"requested_party": "supplier"},
        current,
        {"supplier": "New supplier"},
    )

    assert evidence == [{"field": "supplier", "target": 110}]
    assert controls[0]["id"] == 110
    assert 102 in controls[0]["text_ids"]
    assert library.bind_visible_value_evidence(
        "find-party",
        {"requested_party": "supplier"},
        current,
        {"supplier": "Different supplier"},
    ) == ([], [])


def test_trace_graph_rejects_unknown_predecessor():
    payload = learned().model_dump(mode="json")
    payload["action_groups"][0]["trace_index"] = 0
    payload["action_groups"][0]["predecessor_group_ids"] = ["missing-group"]

    with pytest.raises(ValidationError, match="method_predecessor_unknown"):
        LearnedMethod.model_validate(payload)


def test_local_variant_is_available_for_same_session_and_keeps_original(tmp_path):
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    original = learned()
    library.upsert(original)
    old_group = original.action_groups[0]
    target = old_group.targets[0].model_copy(update={"anchor_texts": ["Supplier"]})
    group = old_group.model_copy(
        update={"id": "supplier-variant", "targets": [target, old_group.targets[1]]}
    )
    updated = library.upsert(original.model_copy(update={"action_groups": [group]}))
    assert updated.revision == 2
    assert len(updated.action_groups) == 2
    current = scene()
    current["ocr"][0]["text"] = "Supplier"
    bound = library.bind_with_context("find-party", {}, current, {"party": "A new party"})
    assert bound is not None
    assert bound.group_id == "supplier-variant"


def test_missing_or_ambiguous_current_anchor_returns_to_model(tmp_path):
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    library.upsert(learned())
    missing = scene()
    missing["ocr"][0]["text"] = "Different concept"
    assert library.bind("find-party", {}, missing, {"party": "value"}) is None
    duplicate = scene()
    duplicate["ocr"].append({"id": 8, "text": "Customer", "bbox": [10, 100, 80, 20]})
    duplicate["markers"].append(
        {"id": 18, "source": "omniparser", "bbox": [100, 95, 200, 30], "text_ids": []}
    )
    assert library.bind("find-party", {}, duplicate, {"party": "value"}) is None
    far = scene()
    far["markers"][0]["bbox"] = [900, 500, 200, 30]
    assert library.bind("find-party", {}, far, {"party": "value"}) is None
    assert library.bind("find-party", {}, scene(), {"unrelated": "value"}) is None


def test_learned_geometry_rebinds_wide_inputs_and_repeated_table_labels(tmp_path):
    # Structure reproduced from the recorded form failure: label-centered distance
    # picked a small lower box, and repeated table captions hid two real inputs.
    screen = {
        "ocr": [
            {"id": 1, "text": "Destination", "bbox": [46, 441, 74, 34]},
            {"id": 2, "text": "Mass", "bbox": [588, 114, 91, 40]},
            {"id": 3, "text": "Count", "bbox": [50, 609, 53, 32]},
            {"id": 4, "text": "Mass", "bbox": [930, 689, 90, 35]},
            {"id": 5, "text": "Count", "bbox": [509, 688, 54, 36]},
            {"id": 6, "text": "Source value A", "bbox": [55, 487, 120, 20]},
            {"id": 7, "text": "Registration · New item", "bbox": [50, 50, 150, 30]},
        ],
        "markers": [
            {"id": 24, "source": "omniparser", "bbox": [44, 481, 531, 41], "text_ids": []},
            {"id": 11, "source": "omniparser", "bbox": [581, 155, 533, 42], "text_ids": []},
            {"id": 32, "source": "omniparser", "bbox": [112, 604, 193, 41], "text_ids": []},
            {"id": 33, "source": "omniparser", "bbox": [49, 605, 52, 40], "text_ids": []},
        ],
    }
    response = {
        "screen_kind": "editor",
        "controls": [
            {"id": target, "role": "edit", "label_ids": [label], "text_ids": []}
            for target, label in ((24, 1), (11, 2), (32, 3))
        ],
        "steps": [
            {"target": target, "action": "fill", "intent": "fill", "source": source}
            for target, source in ((24, "destination"), (11, "mass"), (32, "count"))
        ],
    }
    method = method_from_match(
        screen,
        response,
        method_id="entry",
        purpose="Enter supplied attributes",
        context_anchors=["New item"],
    )
    assert method is not None
    library = MethodLibrary(tmp_path / "library.json", enabled=True)
    library.upsert(method)
    assert "Source value A" not in library.path.read_text(encoding="utf-8")
    for scale in (1, 2):
        current = {kind: [] for kind in screen}
        for kind, items in screen.items():
            for item in items:
                x, y, width, height = item["bbox"]
                current[kind].append(
                    {
                        **item,
                        "id": item["id"] + 1000,
                        "bbox": [x * scale + 31, y * scale + 41, width * scale, height * scale],
                    }
                )
        bound = library.bind("entry", {}, current, {"destination": "B", "mass": "2", "count": "3"})
        assert bound is not None
        assert [step["target"] for step in bound["steps"]] == [1024, 1011, 1032]


def test_candidate_compiler_removes_instance_anchors_and_repeated_retries():
    method = learned()
    group = method.action_groups[0]
    retry = group.model_copy(update={"id": "retry"})
    instance_target = group.targets[0].model_copy(
        update={"anchor_texts": ["CASE-HBL-705", "Synthetic proced.."]}
    )
    instance_group = group.model_copy(
        update={"id": "instance", "targets": [instance_target, *group.targets[1:]]}
    )
    compiled = compile_method_candidate(
        method.model_copy(update={"action_groups": [group, retry, instance_group]}),
        {"hbl_no": "CASE-HBL-705", "description": "Synthetic procedure parts"},
    )

    assert compiled.action_groups == [group]


def completion_scene(*, customer="New customer B", reference="ORDER-B", ids=0):
    return {
        "ocr": [
            {"id": 1 + ids, "text": "Client name", "bbox": [10, 30, 80, 20]},
            {"id": 2 + ids, "text": customer, "bbox": [110, 30, 120, 20]},
            {"id": 3 + ids, "text": "Reference", "bbox": [10, 80, 80, 20]},
            {"id": 4 + ids, "text": reference, "bbox": [110, 80, 120, 20]},
        ],
        "markers": [
            {
                "id": 10 + ids,
                "source": "omniparser",
                "bbox": [105, 25, 140, 30],
                "text_ids": [2 + ids],
            },
            {
                "id": 11 + ids,
                "source": "omniparser",
                "bbox": [105, 75, 140, 30],
                "text_ids": [4 + ids],
            },
        ],
    }


def completion_response(*, ids=0):
    return {
        "screen_kind": "editor",
        "procedure_step": "task-action",
        "purpose": "Verify the saved business record",
        "taught_step_id": "verify-record",
        "controls": [
            {
                "id": 10 + ids,
                "role": "text",
                "label_ids": [1 + ids],
                "text_ids": [2 + ids],
                "confidence": 1,
            },
            {
                "id": 11 + ids,
                "role": "text",
                "label_ids": [3 + ids],
                "text_ids": [4 + ids],
                "confidence": 1,
            },
        ],
        "operation": "complete",
        "evidence": [
            {"field": "customer", "target": 10 + ids},
            {"field": "reference", "target": 11 + ids},
        ],
        "completion_evidence": [
            {"condition_index": 0, "target": 10 + ids},
            {"condition_index": 0, "target": 11 + ids},
        ],
        "expected_result": "The current saved record matches the supplied values.",
    }


def test_completion_recipe_rebinds_labels_and_requires_current_values(tmp_path):
    method = learned().model_copy(
        update={
            "slots": [
                MethodSlot(name="customer", description="customer", source_key="customer"),
                MethodSlot(name="reference", description="reference", source_key="reference"),
            ]
        }
    )
    method = method_completion_from_match(completion_scene(), completion_response(), method)
    assert method is not None and method.completion is not None
    compiled = compile_method_candidate(
        method, {"customer": "New customer B", "reference": "ORDER-B"}
    )
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    library.upsert(compiled)

    current = completion_scene(customer="Later customer", reference="ORDER-C", ids=100)
    bound = library.bind_completion_with_context(
        method.id,
        {},
        current,
        {"customer": "Later customer", "reference": "ORDER-C"},
    )
    assert bound is not None
    response = ScreenMatch.model_validate(bound)
    assert response.operation == "complete"
    assert {item.field for item in response.evidence} == {"customer", "reference"}
    assert {item.target for item in response.evidence} == {110, 111}
    assert (
        library.bind_completion_with_context(
            method.id,
            {},
            current,
            {"customer": "Wrong customer", "reference": "ORDER-C"},
        )
        is None
    )
    stored = library.path.read_text(encoding="utf-8")
    assert "New customer B" not in stored
    assert "ORDER-B" not in stored


def test_coordinate_method_keeps_each_action_point_and_ignores_numbered_catalog(tmp_path):
    screen = {"image_size": [400, 200], "screen_phash": 123, "ocr": [], "markers": []}
    reply = {
        "screen_kind": "editor",
        "controls": [],
        "steps": [
            {"target": 91, "action": "click", "intent": "navigate", "point": [20, 30]},
            {"target": 91, "action": "double_click", "intent": "navigate", "point": [21, 31]},
        ],
    }
    # Internal learning consumes the already-executed contract, not a provider reply.
    method = method_from_match(screen, reply, method_id="points", purpose="Open detail")
    assert method is not None
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    library.upsert(method)
    replay = library.bind("points", {}, screen, {})
    assert replay is not None
    assert [step["point"] for step in replay["steps"]] == [[20, 30], [21, 31]]
