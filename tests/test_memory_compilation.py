import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PIL import Image
from test_visual_scene import wire

from brain.cache.methods import MethodDocument, MethodLibrary
from brain.cache.pattern_matching import ScreenPatternDocument, ScreenPatternEntry
from brain.demo_learning.memory_compilation import (
    MemoryAssembly,
    MemoryCandidate,
    assemble_memory,
    compile_memory,
    read_memory_candidates,
)
from brain.graph.memory_retrieval import (
    MemoryRetrieval,
    RetrievalContext,
    RetrievalState,
    open_memory_library,
)
from brain.graph.save_review import SaveReviewResponse, validate_save_review
from brain.graph.screen_decision import ScreenMatch, compile_match
from brain.graph.screen_mapping import ScreenMapping, mapping_prompt
from brain.task_instruction import TaskInstruction
from brain.visual.frame import frame_input
from brain.visual.scene import Detection, VisualControl, bind_scene
from pipeline.schemas import OcrBlock
from sawon_runtime.storage import RunLog, atomic_write_json


def response(*, target, source=None, intent="fill", point=(200, 300), taught="edit"):
    action = "fill" if source else "click"
    return {
        "screen_kind": "editor",
        "controls": [
            {
                "id": target,
                "role": "edit" if source else "button",
                "label_ids": [],
                "text_ids": [],
                "confidence": 1,
                "confidence_policy": "argmax",
            }
        ],
        "procedure_step": taught,
        "purpose": intent,
        "taught_step_id": taught,
        "steps": [
            {
                "target": target,
                "action": action,
                "intent": intent,
                "source": source,
                "keys": None,
                "point": list(point),
            }
        ],
        "operation": "act",
        "evidence": [],
        "completion_evidence": [],
        "message_targets": [],
        "repair_fields": [],
        "expected_result": intent + " completed",
    }


def screen(caption):
    return {
        "image_size": [800, 600],
        "screen_phash": 123,
        "ocr": [{"id": 1, "text": caption, "bbox": [10, 10, 160, 20]}],
        "markers": [],
    }


def source():
    task = {
        "schema_version": "0.1",
        "name": "Shipment entry",
        "objective": "Enter and save the supplied shipment.",
        "requirements": [],
        "completion_conditions": ["The saved shipment is visible."],
        "constraints": [],
        "procedure": [],
    }
    encoded = json.dumps(task, ensure_ascii=False).encode()
    return {
        "source_case": "cold-case",
        "task": task,
        "task_sha256": hashlib.sha256(encoded).hexdigest(),
        "values": {"hbl_no": "HBL-001"},
    }


def candidates():
    return [
        MemoryCandidate(
            "c000",
            "a" * 64,
            "obs-1",
            0,
            screen("Shipment editor"),
            response(target=90, source="hbl_no"),
        ),
        MemoryCandidate(
            "c001",
            "b" * 64,
            "obs-2",
            1,
            screen("Shipment editor"),
            response(target=91, source=None, intent="save", point=(700, 550), taught="save"),
        ),
    ]


def draft(*, groups=("c000", "c001")):
    return MemoryAssembly(
        purpose="Enter and save a shipment",
        groups=[
            {
                "candidate_id": candidate_id,
                "purpose": "Enter the reference" if candidate_id == "c000" else "Save the record",
                "context_anchors": [],
                "expected_result": "The field is filled"
                if candidate_id == "c000"
                else "The record is saved",
            }
            for candidate_id in groups
        ],
    )


def test_memory_compiler_preserves_purpose_points_and_trace_order(tmp_path):
    from pydantic import ValidationError

    raw = draft().model_dump()
    raw["purpose"] = "목적" * 150  # Synthetic 300-character explanation, not a token/byte limit.
    source_candidates = candidates()
    # Synthetic model observation must survive offline compilation, without source instance values.
    source_candidates[0].response["observed_step_results"] = [
        {
            "step_id": "open-detail",
            "observed_result": "HBL-001 detail opened",
            "completed": True,
        }
    ]
    method = compile_memory(source(), source_candidates, raw, method_id="warm-shipment")
    assert [slot.source_key for slot in method.slots] == ["hbl_no"]
    assert [group.trace_index for group in method.action_groups] == [0, 1]
    assert method.action_groups[1].predecessor_group_ids == [method.action_groups[0].id]
    assert [group.targets[0].point for group in method.action_groups] == [(200, 300), (700, 550)]
    path = tmp_path / "warm-methods.json"
    atomic_write_json(path, MethodDocument(methods=[method]))
    restored = MethodDocument.model_validate_json(path.read_bytes()).methods[0]
    assert restored.purpose == raw["purpose"]
    current = screen("Shipment editor")
    current["ocr"] = []
    current["screen_kind"] = "editor"
    bound = MethodLibrary(path, enabled=True).bind_with_context(
        "warm-shipment",
        {},
        current,
        {"hbl_no": "HBL-002"},
        group_id=method.action_groups[0].id,
    )
    assert bound is not None
    assert bound.response["steps"][0]["point"] == [200.0, 300.0]
    assert bound.response["steps"][0]["source"] == "hbl_no"
    assert [item.model_dump() for item in restored.action_groups[0].observed_step_results] == [
        {
            "step_id": "open-detail",
            "observed_result": "{hbl_no} detail opened",
            "completed": True,
        }
    ]
    assert "observed_step_results" not in bound.response
    assert ScreenMatch.model_validate(bound.response).observed_step_results == []
    assert "HBL-001" not in path.read_text(encoding="utf-8")

    raw["purpose"] += "끝"
    with pytest.raises(ValidationError) as caught:
        compile_memory(source(), candidates(), raw, method_id="too-long")
    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        (("purpose",), "string_too_long")
    ]


@pytest.mark.parametrize("pruned", [False, True], ids=["unpruned", "pruned"])
def test_synthetic_compilation_preserves_field_structure_without_source_run_readings(
    tmp_path, pruned
):
    # Synthetic wrong button name corrected during compilation; its executed point stays fixed.
    original = screen("Shipment editor")
    mapping = ScreenMapping.model_validate(
        {
            "screen_kind": "editor",
            "screen_path": ["Shipment", "Details"],
            "fields": {
                "Reference": {
                    "source": "hbl_no",
                    "role": "text",
                    "bbox": [100, 40, 200, 30],
                    "value": "HBL-001",
                },
                "Open": {"role": "button", "bbox": [600, 500, 100, 40]},
                "Blank": {"role": "edit", "bbox": [300, 40, 80, 30], "value": ""},
            },
            "steps": [
                {"target": "Open", "action": "click", "intent": "navigate", "point": [650, 520]}
            ],
        }
    )
    payload = mapping.model_dump(exclude_unset=True)
    candidate = MemoryCandidate(
        "c001" if pruned else "c000", "a" * 64, "obs", int(pruned), original, payload
    )
    inputs = (
        [MemoryCandidate("c000", "b" * 64, "entry-obs", 0, original, payload), candidate]
        if pruned
        else [candidate]
    )
    reviewed = draft(groups=(candidate.candidate_id,)).model_dump()
    reviewed["groups"][0].update(
        purpose="Close the editor",
        expected_result="The menu is visible",
        field_renames=[{"old_name": "Open", "new_name": "Close"}],
    )
    method = compile_memory(source(), inputs, reviewed, method_id="passive")
    group = method.action_groups[0]
    assert group.screen_path == ["Shipment", "Details"]
    assert group.field_sources == {"Reference": "hbl_no"}
    assert {f.name for f in group.observed_fields} == {"Reference", "Close", "Blank"}
    assert {field.name: field.bbox for field in group.observed_fields} == {
        "Reference": (100, 40, 200, 30),
        "Close": (600, 500, 100, 40),
        "Blank": (300, 40, 80, 30),
    }
    assert "field_values" not in group.model_dump()
    assert "HBL-001" not in method.model_dump_json()
    assert [(target.name, target.role, target.bbox) for target in group.targets] == [
        ("Close", "button", (600, 500, 100, 40))
    ]
    assert [
        (action.target, action.action, action.intent, action.point) for action in group.actions
    ] == [("Close", "click", "navigate", (650, 520))]
    assert "Open" in candidate.response["fields"]
    assert original["markers"] == []
    assert "selected_fields" not in original
    library = MethodLibrary(tmp_path / "passive-memory.json", enabled=True)
    library.upsert(method)
    restored = library.methods()[0].action_groups[0]
    assert restored.observed_fields == group.observed_fields
    assert restored.field_sources == {"Reference": "hbl_no"}
    assert "field_values" not in library.path.read_text(encoding="utf-8")
    runtime = MemoryRetrieval(
        RetrievalContext(
            task=TaskInstruction.model_validate(source()["task"]),
            task_sha256=source()["task_sha256"],
            inputs={"hbl_no": "HBL-002"},
            credentials={},
            observation_id=None,
        ),
        RetrievalState(),
        library,
        case_dir=tmp_path,
        log=RunLog(tmp_path, case_id="semantic-rename", agent_id="test"),
    )
    current = screen("Shipment editor")
    current["ocr"].append({"id": 2, "text": "Close", "bbox": [620, 510, 50, 20]})
    memory = runtime.retrieve_screen_memory(current)
    assert memory is not None
    expected_fields = {
        "Reference": ("text", [100, 40, 200, 30], "hbl_no"),
        "Close": ("button", [600, 500, 100, 40], None),
        "Blank": ("edit", [300, 40, 80, 30], None),
    }
    assert {
        field["name"]: (field["role"], field["bbox"], field["source"]) for field in memory["fields"]
    } == expected_fields
    prompt = mapping_prompt(
        {
            "screen": frame_input(
                {
                    **current,
                    "screen_path": group.screen_path,
                    "memory_fields": memory["fields"],
                    "retrieved_procedure": memory["procedure"],
                }
            )
        }
    )
    supplied = json.loads(prompt[prompt.index("{") :])["screen"]
    assert supplied["memory_fields"] == memory["fields"]
    assert supplied["retrieved_procedure"][0]["steps"] == [
        {"target": "Close", "action": "click", "intent": "navigate", "point": [650, 520]}
    ]
    assert "HBL-001" not in prompt
    bound = runtime.match_pattern(current)
    assert bound is not None and bound.mapping is not None
    assert {control.visual_description for control in bound.controls} == set(expected_fields)
    assert len(current["selected_fields"]) == len(expected_fields)
    assert len(current["markers"]) == len(expected_fields)
    assert all("value" not in field.model_fields_set for field in bound.mapping.fields.values())


def test_synthetic_compiled_save_review_rebinds_current_message_and_keeps_navigation(tmp_path):
    # Synthetic successful cold response; no model or desktop is involved.
    cold_screen = {
        "image_size": [400, 200],
        "screen_phash": 123,
        "ocr": [
            {"id": 1, "text": "Saved HBL-001", "bbox": [10, 10, 270, 20]},
            {"id": 2, "text": "OK", "bbox": [100, 80, 60, 20]},
        ],
        "markers": [
            {"id": 10, "source": "ocr", "bbox": [10, 10, 270, 20], "text_ids": [1]},
            {"id": 20, "source": "omniparser", "bbox": [100, 80, 60, 20], "text_ids": []},
        ],
    }
    cold_response = response(target=20, intent="navigate", point=(135, 90), taught="inspect")
    cold_response.update(screen_kind="dialog", save_result="ready_for_query", message_targets=[10])
    cold_response["controls"].append({"id": 10, "role": "text", "text_ids": [1], "confidence": 1})
    inputs = [
        *candidates(),
        MemoryCandidate("c002", "c" * 64, "obs-3", 2, cold_screen, cold_response),
    ]
    method = compile_memory(
        source(), inputs, draft(groups=("c000", "c001", "c002")), method_id="warm-save-review"
    )
    group = method.action_groups[-1]
    assert group.save_result == "ready_for_query"
    message = next(target for target in group.targets if target.name in group.message_targets)
    assert message.anchor_texts == ["Saved {hbl_no}"]

    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    library.upsert(method)
    current = {
        **cold_screen,
        "markers": [],
        "ocr": [
            {"id": 101, "text": "Saved HBL-002", "bbox": [10, 10, 270, 20]},
            {"id": 102, "text": "OK", "bbox": [100, 80, 60, 20]},
        ],
    }
    rebound = library.bind_with_context(
        method.id, {}, current, {"hbl_no": "HBL-002"}, group_id=group.id
    )
    assert rebound is not None
    plan = ScreenMatch.model_validate(rebound.response)
    assert plan.message_targets and plan.message_targets != [10]
    assert plan.steps[0].point == [135, 90]
    boxes = {item["id"]: item["bbox"] for item in current["markers"]}
    observation = bind_scene(
        wire(),
        [OcrBlock(**block, confidence=1) for block in current["ocr"]],
        Detection(
            screen_kind=plan.screen_kind,
            controls=[
                VisualControl(**control.model_dump(), bbox=boxes[control.id])
                for control in plan.controls
            ],
        ),
    )
    review = compile_match(plan, observation, SaveReviewResponse)
    validate_save_review(review, observation)
    assert review.result == "ready_for_query"
    assert review.next_decision.to_decision().intent == "navigate"
    assert set(review.evidence_refs) <= {item.ref for item in observation.elements}

    # The same geometry must not turn an error or unreadable message into a saved result.
    for label, text in [("different-result", "Save rejected"), ("missing-message", "")]:
        changed = {**current, "ocr": [{**current["ocr"][0], "text": text}, current["ocr"][1]]}
        fallback = library.bind_with_context(
            method.id, {}, changed, {"hbl_no": "HBL-002"}, group_id=group.id
        )
        assert fallback is not None, label
        assert ScreenMatch.model_validate(fallback.response).save_result is None, label


def test_memory_compiler_keeps_only_visible_non_instance_context_anchors():
    selected = draft()
    selected.groups[0].context_anchors = ["Shipment", "Login screen", "HBL-001"]

    method = compile_memory(source(), candidates(), selected, method_id="warm-shipment")

    assert method.action_groups[0].context_anchors == ["Shipment"]


def test_synthetic_pruned_click_rebases_entry_memory_without_rebinding_action_ids(tmp_path):
    # Synthetic 135-shaped trace: remove row selection, retain the following double click.
    # Detector ID 91 belongs to different controls across the two source frames.
    before = {
        **screen("Query results"),
        "screen_phash": 0,
        "screen_path": ["Shipment", "Query"],
        "selected_fields": {
            "17": {"name": "Select row", "role": "row", "bbox": [160, 170, 60, 40]},
            "91": {
                "name": "Selection",
                "role": "text",
                "bbox": [500, 500, 30, 20],
                "value": "unselected",
            },
            # The entry message and retained action have the same model name,
            # but the entry message must not overwrite the action's geometry.
            "29": {"name": "Row", "role": "text", "bbox": [10, 80, 300, 30], "value": "Saved"},
        },
    }
    before["ocr"].append({"id": 2, "text": "Saved", "bbox": [10, 80, 100, 20]})
    after = {
        **screen("Query results"),
        "screen_phash": 1023,
        "screen_path": ["Shipment", "Query"],
        "selected_fields": {
            "91": {"name": "Row", "role": "row", "bbox": [160, 170, 60, 40]},
            "92": {
                "name": "Selection",
                "role": "text",
                "bbox": [500, 500, 30, 20],
                "value": "selected",
            },
        },
    }
    select = response(target=17, intent="navigate", point=(200, 187), taught="open-detail")
    select["controls"].append({"id": 29, "role": "text", "text_ids": [2], "confidence": 1})
    select.update(save_result="ready_for_query", message_targets=[29])
    select["observed_step_results"] = [
        {"step_id": "query", "observed_result": "The row is visible", "completed": True}
    ]
    open_detail = response(target=91, intent="navigate", point=(190, 187), taught="open-detail")
    open_detail["steps"][0]["action"] = "double_click"
    open_detail["observed_step_results"] = [
        {"step_id": "select-row", "observed_result": "The row is selected", "completed": True}
    ]
    inputs = [
        candidates()[0],
        MemoryCandidate("c001", "b" * 64, "before-click", 1, before, select),
        MemoryCandidate("c002", "c" * 64, "after-click", 2, after, open_detail),
    ]
    method = compile_memory(source(), inputs, draft(groups=("c000", "c002")), method_id="pruned")
    group = method.action_groups[-1]
    assert group.screen_phash == before["screen_phash"]
    assert group.message_targets and group.message_targets != ["Row"]
    assert {field.name: field.bbox for field in group.observed_fields} == {
        "Select row": (160, 170, 60, 40),
        "Selection": (500, 500, 30, 20),
        group.message_targets[0]: (10, 80, 300, 30),
    }
    assert "field_values" not in group.model_dump()
    assert "unselected" not in group.model_dump_json()
    assert group.save_result == "ready_for_query"
    assert [item.step_id for item in group.observed_step_results] == ["query"]
    assert group.actions[0].action == "double_click"
    assert group.actions[0].point == (190, 187)
    assert group.targets[0].bbox == (160, 170, 60, 40)
    assert group.predecessor_group_ids == [method.action_groups[0].id]
    library = MethodLibrary(tmp_path / "methods.json", enabled=True)
    library.upsert(method)
    current = {**before, "markers": [], "selected_fields": {}}
    rebound = library.bind_with_context(
        method.id, {}, current, source()["values"], group_id=group.id
    )
    assert rebound is not None
    assert rebound.response["steps"][0]["action"] == "double_click"
    assert rebound.response["steps"][0]["point"] == [190.0, 187.0]
    assert rebound.response["save_result"] == "ready_for_query"


@pytest.mark.parametrize("unknown_candidate", [False, True], ids=["accepted", "bad-target-id"])
def test_anchor_overflow_records_warning_without_relaxing_candidate_ids(
    tmp_path, caplog, unknown_candidate
):
    raw = draft().model_dump()
    anchors = [f"Caption {index}" for index in range(9)]
    raw["groups"][0]["context_anchors"] = anchors
    if unknown_candidate:
        raw["groups"][0]["candidate_id"] = "not-in-source"
    inputs = candidates()
    inputs[0].screen["ocr"][0]["text"] = " / ".join(anchors)
    model = SimpleNamespace(
        complete_json=lambda prompt, schema: SimpleNamespace(response=schema.model_validate(raw))
    )
    log = RunLog(tmp_path, case_id="anchor-warning", agent_id="test")

    def assemble():
        return assemble_memory(
            source(),
            inputs,
            model=model,
            log=log,
            method_id="warning-test",
            output=tmp_path / "method.json",
            max_cost_usd=Decimal(0),
        )

    if unknown_candidate:
        with pytest.raises(ValueError, match="warm_method_selected_unknown_candidate"):
            assemble()
        assert not (tmp_path / "method.json").exists()
    else:
        method = assemble()
        assert method.action_groups[0].context_anchors == anchors[:8]
        assert method.action_groups[0].targets[0].point == (200, 300)
        assert method.action_groups[0].actions[0].slot == "hbl_no"
        raw["groups"][0]["context_anchors"] = anchors[:8]
        expected = compile_memory(source(), inputs, raw, method_id="warning-test")
        assert method.action_groups == expected.action_groups
    warnings = [
        event.payload
        for event in log.read_all()
        if event.payload.get("check") == "warm_context_anchors_truncated"
    ]
    assert warnings == [
        {
            "check": "warm_context_anchors_truncated",
            "level": "warning",
            "group_index": 0,
            "retained": 8,
            "discarded": 1,
        }
    ]
    assert "warm_context_anchors_truncated retained=8 discarded=1" in caplog.text


def test_anchor_overflow_does_not_discard_invalid_trailing_item():
    from pydantic import ValidationError

    raw = draft().model_dump()
    raw["groups"][0]["context_anchors"] = ["valid"] * 8 + [None]
    with pytest.raises(ValidationError) as caught:
        MemoryAssembly.model_validate(raw)
    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        (("groups", 0, "context_anchors", 8), "string_type")
    ]


def test_memory_compiler_rejects_missing_input():
    with pytest.raises(ValueError, match="warm_method_missing_sources:hbl_no"):
        compile_memory(source(), candidates(), draft(groups=("c001",)), method_id="bad")
    without_capture = candidates()
    without_capture[0].screen.pop("screen_phash")
    with pytest.raises(ValueError, match="warm_method_candidate_requires_reference_screenshot"):
        compile_memory(source(), without_capture, draft(), method_id="bad")


def create_successful_case(tmp_path, *, reinterpreted=False):
    case = tmp_path / "cold"
    atomic_write_json(case / "result.json", {"status": "success"})
    task = source()["task"]
    atomic_write_json(case / "task-instruction.json", task)
    atomic_write_json(
        case / "record.json",
        {
            "schema_version": "0.1",
            "case_id": "cold-case",
            "inputs": {"hbl_no": "HBL-001"},
            "origin": "manual",
        },
    )
    mapping = ScreenMapping.model_validate(
        {
            "screen_kind": "editor",
            "fields": {
                "Reference": {"source": "hbl_no", "role": "edit", "bbox": [150, 280, 180, 40]}
            },
            "steps": [
                {"target": "Reference", "action": "fill", "intent": "fill", "point": [200, 300]}
            ],
        }
    )
    current = screen("Shipment editor")
    if reinterpreted:
        # Synthetic action recovery reinterprets the same capture after an earlier mapping.
        mapping.local_payload(current)
    match = mapping.local_payload(current)
    if reinterpreted:
        # Historical repeated projections used different IDs from the raw marker catalog.
        for control in match["controls"]:
            control["id"] += 10
        for step in match["steps"]:
            step["target"] += 10
    visual = case / "visual" / "obs-1"
    atomic_write_json(visual / "markers.json", {"image_size": [800, 600], "markers": []})
    atomic_write_json(visual / "ocr.json", {"blocks": screen("Shipment editor")["ocr"]})
    atomic_write_json(visual / "screen-match.json", match)
    Image.new("RGB", (800, 600), "white").save(visual / "capture.png")
    education_sha = "c" * 64
    (case / "run_log.jsonl").write_text(
        "\n".join(
            json.dumps(event)
            for event in (
                {
                    "kind": "verify",
                    "payload": {
                        "check": "user_education_loaded",
                        "sha256": education_sha,
                    },
                },
                {"kind": "observe", "payload": {"obs_id": "obs-1"}},
                {
                    "kind": "verify",
                    "payload": {
                        "transition": {
                            "action_id": "a1",
                            "before_obs_id": "obs-1",
                            "delivery": "delivered",
                            "intent": "fill",
                            "source": "hbl_no",
                        }
                    },
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / "visual-cache.json"
    atomic_write_json(
        cache_path,
        ScreenPatternDocument(
            entries=(
                ScreenPatternEntry(
                    key="a" * 64, response={**match, "mapping": mapping.model_dump(mode="json")}
                ),
            )
        ),
    )

    return case, cache_path, visual, mapping, match, education_sha


def test_synthetic_completion_survives_recorded_trace_compilation_without_old_values(tmp_path):
    # Synthetic successful trace and semantic reply; no provider or desktop calls.
    case, cache_path, _, mapping, match, _ = create_successful_case(tmp_path)
    final = mapping.model_copy(
        update={
            "screen_path": ["Saved record"],
            "operation": "complete",
            "steps": [],
            "fields": {
                "Reference": mapping.fields["Reference"].model_copy(update={"value": "HBL-001"})
            },
        }
    )
    raw = final.model_dump()
    raw["completion_evidence"] = [{"condition_index": 0, "target": "Reference"}]
    final = ScreenMapping.model_validate(raw)
    final_payload = final.local_payload(screen("Shipment editor"))
    events = [json.loads(line) for line in (case / "run_log.jsonl").read_text().splitlines()]
    events.insert(
        2, {"payload": {"check": "screen_plan_proposed", "obs_id": "obs-1", "response": match}}
    )
    events.append(
        {
            "payload": {
                "check": "screen_plan_proposed",
                "obs_id": "obs-1",
                "response": final_payload,
                "mapping": final.model_dump(),
            }
        }
    )
    (case / "run_log.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    # Completion was never promoted as an action-cache entry; the plan log owns its mapping.
    loaded_source, loaded = read_memory_candidates(case, cache_path)
    compiled = compile_memory(
        loaded_source, loaded, draft(groups=(loaded[0].candidate_id,)), method_id="terminal"
    )
    restored = MethodDocument.model_validate_json(
        MethodDocument(methods=[compiled]).model_dump_json()
    ).methods[0]
    assert restored.completion.screen_path == ["Saved record"]
    assert restored.completion.targets[0].target.bbox == (150, 280, 180, 40)
    assert restored.completion.targets[0].slot == "hbl_no"
    assert restored.completion.targets[0].condition_indexes == [0]
    assert "field_values" not in restored.completion.model_dump()
    assert "HBL-001" not in restored.model_dump_json()


def test_synthetic_accumulated_completion_uses_only_final_verified_observation(tmp_path):
    # Synthetic 135-shaped trace using existing fakes, not a live ERP recording.
    case, cache_path, _, mapping, match, _ = create_successful_case(tmp_path)
    final = mapping.model_copy(
        update={
            "screen_path": ["Saved record"],
            "operation": "complete",
            "steps": [],
            "fields": {
                "Reference": mapping.fields["Reference"].model_copy(update={"value": "HBL-001"})
            },
        }
    )
    original = [json.loads(line) for line in (case / "run_log.jsonl").read_text().splitlines()]
    original.insert(
        2, {"payload": {"check": "screen_plan_proposed", "obs_id": "obs-1", "response": match}}
    )
    final_plan = {
        "payload": {
            "check": "screen_plan_proposed",
            "obs_id": "obs-1",
            "response": final.local_payload(screen("Shipment editor")),
            "mapping": final.model_dump(),
        }
    }
    for label, last_obs_id, verified_fields, accepted in (
        ("accepted-final-observation", "obs-1", ["hbl_no"], True),
        ("different-final-observation", "different", ["hbl_no"], False),
        ("incomplete-review", "obs-1", [], False),
        ("later-action-on-same-frame", "obs-1", ["hbl_no"], False),
    ):
        events = [
            *original,
            final_plan,
            {
                "kind": "verify",
                "payload": {
                    "check": "post_save_model_evidence_complete",
                    "ok": True,
                    "fields": verified_fields,
                },
            },
            {"kind": "stop", "payload": {"status": "success", "last_obs_id": last_obs_id}},
            {"kind": "verify", "payload": {"check": "method_candidate_promotion"}},
        ]
        if label == "later-action-on-same-frame":
            events.insert(
                len(original) + 1,
                {
                    "payload": {
                        "check": "screen_plan_proposed",
                        "obs_id": "obs-1",
                        "response": match,
                    }
                },
            )
        (case / "run_log.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events), encoding="utf-8"
        )
        loaded_source, loaded = read_memory_candidates(case, cache_path)
        method = compile_memory(
            loaded_source, loaded, draft(groups=(loaded[0].candidate_id,)), method_id="final"
        )
        assert (method.completion is not None) == accepted, label
        if accepted:
            assert method.completion.targets[0].condition_indexes == [], label
            assert method.completion.targets[0].slot == "hbl_no", label
            assert method.completion.targets[0].target.bbox == (150, 280, 180, 40), label
            assert "field_values" not in method.completion.model_dump(), label
            assert "HBL-001" not in method.model_dump_json(), label


@pytest.mark.parametrize("reinterpreted", [False, True], ids=["fresh", "reinterpreted"])
def test_successful_case_is_joined_only_to_promoted_cache(tmp_path, reinterpreted):
    case, cache_path, visual, mapping, match, education_sha = create_successful_case(
        tmp_path, reinterpreted=reinterpreted
    )
    loaded_source, loaded = read_memory_candidates(case, cache_path)
    target = loaded[0].response["steps"][0]["target"]
    assert target == "Reference"
    assert loaded[0].response["fields"][target]["bbox"] == [150, 280, 180, 40]
    assert loaded[0].screen["markers"] == []
    assert "selected_fields" not in loaded[0].screen

    # A successful selection-phase plan is equally eligible. A duplicate artifact
    # on the same frame must not create a second execution step.
    atomic_write_json(visual / "screen-plan.json", match)
    assert len(read_memory_candidates(case, cache_path)[1]) == 1
    (visual / "screen-match.json").unlink()
    assert len(read_memory_candidates(case, cache_path)[1]) == 1

    # Reused frames retain only capture + plan; read_screen must use exact pixels.
    from brain.demo_learning.methods import read_screen

    reused = case / "visual" / "reused"
    reused.mkdir()
    (reused / "capture.png").write_bytes((visual / "capture.png").read_bytes())
    assert read_screen(case, "reused") == read_screen(case, "obs-1")
    atomic_write_json(reused / "screen-plan.json", match)
    assert len(read_memory_candidates(case, cache_path)[1]) == 1  # No delivery on reused frame.

    assert loaded_source["values"] == {"hbl_no": "HBL-001"}
    assert loaded_source["task_sha256"] == education_sha
    assert [(item.candidate_id, item.cache_key, item.obs_id) for item in loaded] == [
        ("c000", "a" * 64, "obs-1")
    ]

    compiled = compile_memory(
        loaded_source,
        loaded,
        {
            "purpose": "Fill reference",
            "groups": [
                {
                    "candidate_id": "c000",
                    "purpose": "Fill reference",
                    "expected_result": "Reference entered",
                    "context_anchors": [],
                }
            ],
        },
        method_id="named-warm",
    )
    retained_target = compiled.action_groups[0].targets[0]
    assert (retained_target.name, retained_target.role, retained_target.bbox) == (
        "Reference",
        "edit",
        (150, 280, 180, 40),
    )
    # Local normalization is authoritative; restoring semantic geometry must not
    # restore the provider's pre-normalization operation.
    mapping.operation = "duplicate_clear"
    atomic_write_json(
        cache_path,
        ScreenPatternDocument(
            entries=(
                ScreenPatternEntry(
                    key="a" * 64, response={**match, "mapping": mapping.model_dump(mode="json")}
                ),
            )
        ),
    )
    assert read_memory_candidates(case, cache_path)[1][0].response["operation"] == "act"
    # A post-action interpretation failure can prevent per-edge promotion even
    # though delivery and the final business result both succeeded.
    atomic_write_json(cache_path, ScreenPatternDocument())
    recovered = read_memory_candidates(case, cache_path)[1]
    assert len(recovered) == 1
    assert recovered[0].cache_key.startswith("trace:")
    path = tmp_path / "compiled.json"
    atomic_write_json(path, MethodDocument(methods=[compiled]))
    current = {
        "image_size": [800, 600],
        "screen_kind": "editor",
        "markers": [],
        "ocr": [],
        "screen_phash": loaded[0].screen["screen_phash"],
    }
    library = open_memory_library(path, tmp_path / "warm", enabled=True, compiled_only=True)
    bound = library.bind_with_context(
        "named-warm", {}, current, {"hbl_no": "HBL-002"}, group_id=compiled.action_groups[0].id
    )
    assert bound is not None
    assert bound.response["steps"][0]["point"] == [200, 300]
    assert bound.response["steps"][0]["source"] == "hbl_no"
    assert compiled.action_groups[0].targets[0].anchor_texts == []


@pytest.mark.parametrize("skip_logged", [False, True], ids=["undelivered", "already-satisfied"])
def test_memory_candidates_keep_navigation_after_satisfied_input(tmp_path, skip_logged):
    case, cache_path, visual, *_ = create_successful_case(tmp_path)
    atomic_write_json(cache_path, ScreenPatternDocument())
    path = visual / "screen-match.json"
    match = json.loads(path.read_text(encoding="utf-8"))
    match["steps"].append(
        {
            "target": 90,
            "action": "double_click",
            "intent": "select_option",
            "source": None,
            "point": [200, 300],
        }
    )
    atomic_write_json(path, match)
    log_path = case / "run_log.jsonl"
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    transition = events[-1]["payload"]["transition"]
    transition.update(intent="select_option", source=None, observed_effect="changed")
    if skip_logged:
        events.append(
            {
                "kind": "verify",
                "payload": {
                    "check": "observed_actions_already_satisfied",
                    "obs_id": "obs-1",
                    "actions": [{"intent": "fill", "source": "hbl_no", "ref": "input-1"}],
                },
            }
        )
    log_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    if not skip_logged:
        with pytest.raises(ValueError, match="successful_run_has_no_joined_visual_cache_actions"):
            read_memory_candidates(case, tmp_path / "visual-cache.json")
    else:
        from brain.demo_learning.memory_compilation import memory_assembly_prompt

        source_data, loaded = read_memory_candidates(case, tmp_path / "visual-cache.json")
        assert len(loaded) == 1
        assert [step["action"] for step in loaded[0].response["steps"]] == ["double_click"]
        assert loaded[0].satisfied_actions[0]["source"] == "hbl_no"
        assert loaded[0].observed_transitions[0]["observed_effect"] == "changed"
        assert '"observed_effect": "changed"' in memory_assembly_prompt(source_data, loaded)


def test_critic_graph_keeps_execution_order_and_excludes_wholly_skipped_plan(tmp_path):
    from brain.demo_learning.memory_compilation import memory_assembly_prompt

    case, cache_path, _, fill_mapping, fill, _ = create_successful_case(tmp_path)
    click_mapping = ScreenMapping.model_validate(
        {
            "screen_kind": "editor",
            "fields": {"Save": {"role": "button", "bbox": [650, 520, 100, 60]}},
            "steps": [{"target": "Save", "action": "click", "intent": "save", "point": [700, 550]}],
        }
    )
    click = click_mapping.local_payload(screen("Shipment editor"))
    header = json.loads((case / "run_log.jsonl").read_text(encoding="utf-8").splitlines()[0])
    events = [header]

    def proposal(plan, *, obs_id="obs-1", mapping=None):
        events.append(
            {
                "kind": "select",
                "payload": {
                    "check": "screen_plan_proposed",
                    "obs_id": obs_id,
                    "response": plan,
                    **({"mapping": mapping.model_dump()} if mapping is not None else {}),
                },
            }
        )

    def action(action_id):
        events.append(
            {"kind": "act", "payload": {"stage": "requested", "action": {"action_id": action_id}}}
        )

    confirmed = {"step_id": "edit", "observed_result": "HBL-001 is filled", "completed": True}
    unconfirmed = {
        "step_id": "save",
        "observed_result": "HBL-001 remains in the editor; saving is not confirmed",
        "completed": False,
    }
    # Synthetic reused observation ID: an earlier claim must not become post-delivery evidence.
    proposal({**fill, "steps": [], "observed_step_results": [unconfirmed]}, obs_id="after-1")
    proposal(fill, mapping=fill_mapping)
    action("a1")
    proposal({**fill, "steps": [], "observed_step_results": [confirmed]}, obs_id="after-1")
    proposal(fill, mapping=fill_mapping)  # Same frame, but this whole plan is skipped.
    events.append(
        {
            "kind": "verify",
            "payload": {
                "check": "observed_actions_already_satisfied",
                "obs_id": "obs-1",
                "actions": [{"intent": "fill", "source": "hbl_no", "ref": "input-1"}],
            },
        }
    )
    proposal(click, mapping=click_mapping)
    action("a2")
    proposal({**click, "steps": [], "observed_step_results": [unconfirmed]}, obs_id="after-2")
    # Reviews arrive after the next proposal, as in the combined observer/planner.
    for action_id, intent, source_key, after_id in (
        ("a1", "fill", "hbl_no", "after-1"),
        ("a2", "save", None, "after-2"),
    ):
        events.append(
            {
                "kind": "verify",
                "payload": {
                    "transition": {
                        "action_id": action_id,
                        "before_obs_id": "obs-1",
                        "after_obs_id": after_id,
                        "delivery": "delivered",
                        "intent": intent,
                        "source": source_key,
                        "observed_effect": "changed",
                    }
                },
            }
        )
    (case / "run_log.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    source_data, loaded = read_memory_candidates(case, cache_path)
    assert len(loaded) == 2
    assert loaded[0].trace_index < loaded[1].trace_index
    assert [c.observed_transitions[0]["action_id"] for c in loaded] == ["a1", "a2"]
    assert len(source_data["nonexecuted_plans"]) == 1
    assert source_data["nonexecuted_plans"][0]["already_satisfied"][0]["source"] == "hbl_no"
    assert list(loaded[0].after_observed_step_results) == [confirmed]
    assert list(loaded[1].after_observed_step_results) == [unconfirmed]
    prompt = memory_assembly_prompt(source_data, loaded)
    assert '"previous_candidate_id": "c000"' in prompt
    assert '"screen_phash":' in prompt
    assert '"after_screen_phash":' in prompt
    assert '"matching_later_candidates":' in prompt
    supplied = json.loads(prompt[prompt.index("{") :])["candidates"]
    assert supplied[0]["ocr"] == [{"text": "Shipment editor", "bbox": [10, 10, 160, 20]}]
    assert supplied[0]["fields"] == [
        {"name": "Reference", "role": "edit", "bbox": [150, 280, 180, 40], "source": "hbl_no"}
    ]
    assert [item["actions"][0]["target"] for item in supplied] == ["Reference", "Save"]
    assert [item["actions"][0]["point"] for item in supplied] == [[200, 300], [700, 550]]
    assert supplied[0]["after_observed_step_results"] == [
        {**confirmed, "observed_result": "{hbl_no} is filled"}
    ]
    assert supplied[1]["after_observed_step_results"] == [
        {
            **unconfirmed,
            "observed_result": "{hbl_no} remains in the editor; saving is not confirmed",
        }
    ]
    method = compile_memory(source_data, loaded, draft(), method_id="critic")
    assert len({g.trace_index for g in method.action_groups}) == 2


def test_critic_prompt_identifies_synthetic_later_candidate_on_same_result_screen(tmp_path):
    """Synthetic trace: expose a possible span boundary without declaring it redundant."""
    from brain.demo_learning.memory_compilation import memory_assembly_prompt
    from brain.demo_learning.methods import read_screen

    after = tmp_path / "visual" / "after-1"
    atomic_write_json(after / "markers.json", {"image_size": [800, 600], "markers": []})
    atomic_write_json(after / "ocr.json", {"blocks": screen("Additional fields")["ocr"]})
    Image.new("RGB", (800, 600), "white").save(after / "capture.png")
    result_screen = read_screen(tmp_path, "after-1")
    synthetic = [
        MemoryCandidate(
            "c000",
            "a" * 64,
            "before-1",
            1,
            screen("Main fields"),
            response(target=1, intent="select_tab"),
            observed_transitions=(
                {
                    "after_obs_id": "after-1",
                    "intent": "select_tab",
                    "observed_effect": "changed",
                },
            ),
        ),
        MemoryCandidate(
            "c001",
            "b" * 64,
            "after-1",
            2,
            result_screen,
            response(target=2, source="hbl_no"),
        ),
    ]
    source_data = {**source(), "source_case": str(tmp_path)}
    prompt = memory_assembly_prompt(source_data, synthetic)
    payload = json.loads(prompt[prompt.index("{") :])

    assert payload["candidates"][0]["after_screen_phash"] == result_screen["screen_phash"]
    assert payload["candidates"][0]["matching_later_candidates"] == [
        {"candidate_id": "c001", "phash_distance": 0}
    ]
    rules = " ".join(prompt[: prompt.index("{")].split())
    assert "Review contiguous candidate spans counterfactually" in rules
    assert "supporting evidence, never proof of redundancy" in rules
    assert "same visible action may establish different effects" in rules
