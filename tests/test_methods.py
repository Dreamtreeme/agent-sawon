from brain.cache.methods import (
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
                "source": "pixel_rectangle",
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
    assert "bbox" not in text
    assert restored.catalog("Prepare a delivery")[0]["id"] == "find-party"
    assert restored.catalog("Unrelated task") == []


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
        {"id": 18, "source": "pixel_rectangle", "bbox": [100, 95, 200, 30], "text_ids": []}
    )
    assert library.bind("find-party", {}, duplicate, {"party": "value"}) is None
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
