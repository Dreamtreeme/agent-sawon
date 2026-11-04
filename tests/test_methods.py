import os

import pytest
from pydantic import ValidationError

from brain.cache.methods import (
    LearnedMethod,
    MethodDocument,
    MethodLibrary,
    MethodSlot,
    compile_method_candidate,
    method_completion_from_match,
    method_from_match,
)
from brain.graph.screen_decision import ScreenMatch
from brain.task_instruction import ObservedStepResult
from sawon_runtime.storage import atomic_write_json


def scene(*, offset=0, old_value="Old customer", ids=0):
    return {
        "image_size": [800, 600],
        "screen_phash": 123,
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


def test_saved_coordinate_method_uses_new_source_without_ocr_rebinding(tmp_path):
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    library.upsert(learned())
    restored = MethodLibrary(library.path, enabled=True)
    method = restored.methods()[0]
    current = {"screen_phash": 123, "image_size": [800, 600], "ocr": [], "markers": []}
    bound = restored.bind_with_context(
        method, {"requested_party": "supplier"}, current, {"supplier": "New supplier"}
    )
    assert bound is not None
    response = ScreenMatch.model_validate(bound.response)
    assert response.steps[0].source == "supplier"
    assert [step.point for step in response.steps] == [[200, 40], [415, 40]]
    assert all(not control.label_ids for control in response.controls)
    assert response.evidence == []
    assert "Old customer" not in library.path.read_text(encoding="utf-8")
    assert restored.catalog("Prepare a delivery")[0]["id"] == "find-party"
    assert restored.catalog("Unrelated task") == []
    assert restored.bind_with_context(method, {}, current, {"unrelated": "value"}) is None
    # The configured distance is inclusive; viewport mismatch must not rescale coordinates.
    for label, update, expected in [
        ("distance-six", {"screen_phash": 123 ^ 63}, True),
        ("distance-seven", {"screen_phash": 123 ^ 127}, False),
        ("resized", {"image_size": [1600, 900]}, False),
    ]:
        result = restored.bind_with_context(method, {}, {**current, **update}, {"party": "Next"})
        assert (result is not None) is expected, label


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


def test_library_reuses_parsed_snapshot_but_detects_external_writes(tmp_path, monkeypatch):
    path = tmp_path / "methods.json"
    first = learned()
    atomic_write_json(path, MethodDocument(methods=[first]))
    library = MethodLibrary(path, enabled=True)
    parse = MethodDocument.model_validate_json
    parsed = []

    def tracked_parse(content):
        parsed.append(content)
        return parse(content)

    monkeypatch.setattr(MethodDocument, "model_validate_json", tracked_parse)
    returned = library.methods()
    returned.clear()
    assert library.methods() == [first]
    assert len(parsed) == 1

    # An external same-size write with a restored timestamp must still invalidate parsing.
    stat = path.stat()
    path.write_bytes(path.read_bytes().replace(b"Find the requested", b"Seek the requested"))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert library.methods()[0].purpose.startswith("Seek ")
    assert len(parsed) == 2

    # Upsert must merge against the latest external contents under the existing file lock.
    other = first.model_copy(update={"id": "external-method"})
    atomic_write_json(path, MethodDocument(methods=[first, other]))
    updated = library.upsert(first)
    assert updated.revision == 2
    assert {item.id for item in library.methods()} == {first.id, other.id}
    path.unlink()
    assert library.methods() == []
    atomic_write_json(path, MethodDocument(methods=[other]))
    assert library.methods() == [other]
    path.write_text("invalid document", encoding="utf-8")
    for _ in range(2):
        with pytest.raises(ValidationError):
            library.methods()
    atomic_write_json(path, MethodDocument(methods=[other]))
    assert library.methods() == [other]


def test_binding_uses_chosen_method_without_reopening_library(tmp_path, monkeypatch):
    library = MethodLibrary(tmp_path / "unused.json", enabled=True)
    method = learned()

    def unexpected_read():
        pytest.fail("The selected method is already the binding snapshot")

    monkeypatch.setattr(library, "methods", unexpected_read)
    current = {"screen_phash": 123, "image_size": [800, 600], "ocr": [], "markers": []}
    bound = library.bind_with_context(method, {}, current, {"party": "Current customer"})
    assert bound is not None
    assert [step["point"] for step in bound.response["steps"]] == [[200, 40], [415, 40]]


def test_trace_graph_rejects_unknown_predecessor():
    payload = learned().model_dump(mode="json")
    payload["action_groups"][0]["trace_index"] = 0
    payload["action_groups"][0]["predecessor_group_ids"] = ["missing-group"]

    with pytest.raises(ValidationError, match="method_predecessor_unknown"):
        LearnedMethod.model_validate(payload)


def test_saved_coordinates_ignore_competing_table_labels(tmp_path):
    # Structure reproduced from the recorded form failure: label-centered distance
    # picked a small lower box, and repeated table captions hid two real inputs.
    screen = {
        "image_size": [1200, 800],
        "screen_phash": 123,
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
    # Competing labels and detector IDs are irrelevant after screenshot matching.
    current = {"image_size": [1200, 800], "screen_phash": 123, "ocr": [], "markers": []}
    bound = library.bind_with_context(
        method, {}, current, {"destination": "B", "mass": "2", "count": "3"}
    )
    assert bound is not None
    assert [step["source"] for step in bound.response["steps"]] == ["destination", "mass", "count"]
    assert [step["point"] for step in bound.response["steps"]] == [
        [309.5, 501.5],
        [847.5, 176],
        [208.5, 624.5],
    ]


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


def test_synthetic_observed_step_results_remain_compile_history_not_current_evidence(
    tmp_path,
):
    # Synthetic successful source: the same action can follow distinct observed outcomes.
    method = learned()
    group = method.action_groups[0]
    assert not group.observed_step_results, "old artifacts have no fabricated observed completion"
    observed = ObservedStepResult(
        step_id="open-detail", observed_result="Detail for CASE-001 opened", completed=True
    )
    still_pending = observed.model_copy(update={"completed": False})
    first = group.model_copy(update={"observed_step_results": [still_pending]})
    next_group = group.model_copy(update={"id": "opened", "observed_step_results": [observed]})
    reworded = next_group.model_copy(
        update={
            "id": "same-completion",
            "observed_step_results": [
                observed.model_copy(update={"observed_result": "Detail ready"})
            ],
        }
    )
    compiled = compile_method_candidate(
        method.model_copy(update={"action_groups": [first, next_group, reworded]}),
        {"reference": "CASE-001"},
    )
    assert len(compiled.action_groups) == 2, (
        "retain distinct completion states, but ignore description-only differences"
    )
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    library.upsert(compiled)
    for index, completed in enumerate((False, True)):
        current = {"screen_phash": 123, "image_size": [800, 600], "ocr": [], "markers": []}
        bound = library.bind_with_context(
            compiled,
            {},
            current,
            {"party": "New customer"},
            group_id=compiled.action_groups[index].id,
        )
        assert bound is not None
        assert [
            result.model_dump() for result in compiled.action_groups[index].observed_step_results
        ] == [
            {
                "step_id": "open-detail",
                "observed_result": "Detail for {reference} opened",
                "completed": completed,
            }
        ], index
        assert "observed_step_results" not in bound.response
        assert ScreenMatch.model_validate(bound.response).observed_step_results == []
    assert "CASE-001" not in library.path.read_text(encoding="utf-8")
    old = method.model_dump(mode="json")
    old["action_groups"][0].pop("observed_step_results")
    assert LearnedMethod.model_validate(old).action_groups[0].observed_step_results == []


def completion_scene(*, customer="New customer B", reference="ORDER-B", ids=0):
    return {
        "image_size": [800, 600],
        "screen_phash": 123,
        "screen_path": ["Saved record"],
        "selected_fields": {
            str(10 + ids): {
                "name": "Client name",
                "role": "text",
                "source": "customer",
                "bbox": [105, 25, 140, 30],
                "value": customer,
            },
            str(11 + ids): {
                "name": "Reference",
                "role": "text",
                "source": "reference",
                "bbox": [105, 75, 140, 30],
                "value": reference,
            },
        },
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


def test_completion_recipe_binds_review_structure_without_copying_prior_readings(
    tmp_path, monkeypatch
):
    method = learned().model_copy(
        update={
            "slots": [
                MethodSlot(name="customer", description="customer", source_key="customer"),
                MethodSlot(name="reference", description="reference", source_key="reference"),
            ]
        }
    )
    source_response = completion_response()
    source_response["observed_step_results"] = [
        {
            "step_id": "verify-record",
            "observed_result": "New customer B and ORDER-B match the saved detail",
            "completed": True,
        }
    ]
    method = method_completion_from_match(
        completion_scene(),
        source_response,
        method,
        {"customer": "New customer B", "reference": "ORDER-B"},
    )
    assert method is not None and method.completion is not None
    compiled = compile_method_candidate(
        method, {"customer": "New customer B", "reference": "ORDER-B"}
    )
    # Synthetic old artifact: loading drops both literal and input-relative readings.
    legacy = compiled.model_dump(mode="json")
    legacy["action_groups"][0]["field_values"] = {"Customer": "Old customer"}
    legacy["completion"]["field_values"] = {
        "Customer": {"source": "customer"},
        "Reference": "ORDER-B",
    }
    restored = LearnedMethod.model_validate(legacy)
    assert restored.model_dump() == compiled.model_dump()
    assert "field_values" not in restored.model_dump_json()
    assert "field_values" in legacy["completion"]  # Loading does not mutate its input.
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    library.upsert(restored)

    current = completion_scene(customer="Later customer", reference="ORDER-C", ids=100)
    bound = library.bind_completion_with_context(
        restored,
        {},
        current,
        {"customer": "Later customer", "reference": "ORDER-C"},
    )
    assert bound is not None
    response = ScreenMatch.model_validate(bound)
    assert response.operation == "complete"
    assert [item.model_dump() for item in restored.completion.observed_step_results] == [
        {
            "step_id": "verify-record",
            "observed_result": "{customer} and {reference} match the saved detail",
            "completed": True,
        }
    ]
    assert "observed_step_results" not in bound
    assert "evidence" not in bound
    assert response.observed_step_results == [] and response.evidence == []
    assert len({item.id for item in response.controls}) == 2
    assert {item.condition_index for item in response.completion_evidence} == {0}
    # These bindings are review locations, not proof that the new input is on screen.
    changed_input = library.bind_completion_with_context(
        restored,
        {"customer": "next_customer", "reference": "next_reference"},
        current,
        {"next_customer": "Not yet observed", "next_reference": "ORDER-D"},
    )
    assert changed_input is not None
    assert changed_input["controls"] == bound["controls"]
    assert changed_input["completion_evidence"] == bound["completion_evidence"]
    assert "evidence" not in changed_input
    assert (
        library.bind_completion_with_context(
            restored,
            {},
            current,
            {"reference": "ORDER-C"},
        )
        is None
    )
    stored = library.path.read_text(encoding="utf-8")
    assert "New customer B" not in stored
    assert "field_values" not in stored

    def unexpected_read():
        pytest.fail("Completion binding must use the selected method snapshot")

    monkeypatch.setattr(library, "methods", unexpected_read)
    assert (
        library.bind_completion_with_context(
            restored,
            {},
            completion_scene(customer="Later customer", reference="ORDER-C", ids=100),
            {"customer": "Later customer", "reference": "ORDER-C"},
        )
        == bound
    )
    assert "ORDER-B" not in stored


def test_synthetic_completion_keeps_user_condition_anchor_without_an_input_source(tmp_path):
    # Synthetic replies cover both a separate status anchor and a status-only completion.
    # An input-key binding is not required to remember where the model saw task completion.
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    for label, evidence in (
        ("separate-status", [{"field": "customer", "target": 10}]),
        ("status-only", []),
    ):
        response = completion_response()
        response["evidence"] = evidence
        response["completion_evidence"] = [{"condition_index": 0, "target": 11}]
        method = method_completion_from_match(
            completion_scene(), response, learned(), {"customer": "New customer B"}
        )
        assert method is not None and method.completion is not None, label
        condition_target = method.completion.targets[-1]
        assert condition_target.slot is None, label
        assert condition_target.condition_indexes == [0], label
        assert condition_target.target.bbox is not None, label
        restored = LearnedMethod.model_validate_json(method.model_dump_json())
        bound = library.bind_completion_with_context(
            restored, {}, completion_scene(ids=100), {"customer": "Current input"}
        )
        assert bound is not None and bound["operation"] == "complete", label
        assert len(bound["completion_evidence"]) == 1, label
        assert "evidence" not in bound and "observed_step_results" not in bound, label
        response["completion_evidence"] = []
        unannotated = method_completion_from_match(completion_scene(), response, learned())
        assert (unannotated is not None) is bool(evidence), label


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
    replay = library.bind_with_context(method, {}, screen, {})
    assert replay is not None
    assert [step["point"] for step in replay.response["steps"]] == [[20, 30], [21, 31]]
