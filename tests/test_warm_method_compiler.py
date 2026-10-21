import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PIL import Image
from test_visual_scene import wire

from brain.cache.methods import MethodDocument, MethodLibrary
from brain.cache.visual_match import VisualMatchDocument, VisualMatchEntry
from brain.demo_learning.warm_methods import (
    WarmCandidate,
    WarmMethodAssembly,
    assemble_warm_method,
    compile_warm_method,
    read_warm_candidates,
)
from brain.graph.matching import ScreenMatch, compile_match
from brain.graph.method_runtime import open_method_library
from brain.graph.save_review import SaveReviewResponse, validate_save_review
from brain.graph.screen_mapping import ScreenMapping
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
        WarmCandidate(
            "c000",
            "a" * 64,
            "obs-1",
            0,
            screen("Shipment editor"),
            response(target=90, source="hbl_no"),
        ),
        WarmCandidate(
            "c001",
            "b" * 64,
            "obs-2",
            1,
            screen("Shipment editor"),
            response(target=91, source=None, intent="save", point=(700, 550), taught="save"),
        ),
    ]


def draft(*, groups=("c000", "c001")):
    return WarmMethodAssembly(
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


def test_warm_compiler_preserves_successful_model_points_and_trace_order(tmp_path):
    method = compile_warm_method(source(), candidates(), draft(), method_id="warm-shipment")
    assert [slot.source_key for slot in method.slots] == ["hbl_no"]
    assert [group.trace_index for group in method.action_groups] == [0, 1]
    assert method.action_groups[1].predecessor_group_ids == [method.action_groups[0].id]
    assert [group.targets[0].point for group in method.action_groups] == [(200, 300), (700, 550)]
    path = tmp_path / "warm-methods.json"
    atomic_write_json(path, MethodDocument(methods=[method]))
    current = screen("Shipment editor")
    current["ocr"] = []
    current["screen_kind"] = "editor"
    bound = MethodLibrary(path, enabled=True).bind(
        "warm-shipment",
        {},
        current,
        {"hbl_no": "HBL-002"},
        group_id=method.action_groups[0].id,
    )
    assert bound is not None
    assert bound["steps"][0]["point"] == [200.0, 300.0]
    assert bound["steps"][0]["source"] == "hbl_no"


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
        WarmCandidate("c002", "c" * 64, "obs-3", 2, cold_screen, cold_response),
    ]
    method = compile_warm_method(
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
    rebound = library.bind(method.id, {}, current, {"hbl_no": "HBL-002"}, group_id=group.id)
    assert rebound is not None
    plan = ScreenMatch.model_validate(rebound)
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
        fallback = library.bind(method.id, {}, changed, {"hbl_no": "HBL-002"}, group_id=group.id)
        assert fallback is not None, label
        assert ScreenMatch.model_validate(fallback).save_result is None, label


def test_warm_compiler_keeps_only_visible_non_instance_context_anchors():
    selected = draft()
    selected.groups[0].context_anchors = ["Shipment", "Login screen", "HBL-001"]

    method = compile_warm_method(source(), candidates(), selected, method_id="warm-shipment")

    assert method.action_groups[0].context_anchors == ["Shipment"]


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
        return assemble_warm_method(
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
        expected = compile_warm_method(source(), inputs, raw, method_id="warning-test")
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
        WarmMethodAssembly.model_validate(raw)
    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        (("groups", 0, "context_anchors", 8), "string_type")
    ]


def test_warm_compiler_rejects_missing_input():
    with pytest.raises(ValueError, match="warm_method_missing_sources:hbl_no"):
        compile_warm_method(source(), candidates(), draft(groups=("c001",)), method_id="bad")
    without_capture = candidates()
    without_capture[0].screen.pop("screen_phash")
    with pytest.raises(ValueError, match="warm_method_candidate_requires_reference_screenshot"):
        compile_warm_method(source(), without_capture, draft(), method_id="bad")


def create_successful_case(tmp_path):
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
            "fields": {"Reference": {"role": "edit", "bbox": [150, 280, 180, 40]}},
            "assignments": {"Reference": "hbl_no"},
            "steps": [
                {"target": "Reference", "action": "fill", "intent": "fill", "point": [200, 300]}
            ],
        }
    )
    match = mapping.local_payload(screen("Shipment editor"))
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
        VisualMatchDocument(
            entries=(
                VisualMatchEntry(
                    key="a" * 64, response={**match, "mapping": mapping.model_dump(mode="json")}
                ),
            )
        ),
    )

    return case, cache_path, visual, mapping, match, education_sha


def test_successful_case_is_joined_only_to_promoted_cache(tmp_path):
    case, cache_path, visual, mapping, match, education_sha = create_successful_case(tmp_path)
    loaded_source, loaded = read_warm_candidates(case, cache_path)

    # A successful selection-phase plan is equally eligible. A duplicate artifact
    # on the same frame must not create a second execution step.
    atomic_write_json(visual / "screen-plan.json", match)
    assert len(read_warm_candidates(case, cache_path)[1]) == 1
    (visual / "screen-match.json").unlink()
    assert len(read_warm_candidates(case, cache_path)[1]) == 1

    # Reused frames retain only capture + plan; read_screen must use exact pixels.
    from brain.demo_learning.methods import read_screen

    reused = case / "visual" / "reused"
    reused.mkdir()
    (reused / "capture.png").write_bytes((visual / "capture.png").read_bytes())
    assert read_screen(case, "reused") == read_screen(case, "obs-1")
    atomic_write_json(reused / "screen-plan.json", match)
    assert len(read_warm_candidates(case, cache_path)[1]) == 1  # No delivery on reused frame.

    assert loaded_source["values"] == {"hbl_no": "HBL-001"}
    assert loaded_source["task_sha256"] == education_sha
    assert [(item.candidate_id, item.cache_key, item.obs_id) for item in loaded] == [
        ("c000", "a" * 64, "obs-1")
    ]

    compiled = compile_warm_method(
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
    # Local normalization is authoritative; restoring semantic geometry must not
    # restore the provider's pre-normalization operation.
    mapping.operation = "duplicate_clear"
    atomic_write_json(
        cache_path,
        VisualMatchDocument(
            entries=(
                VisualMatchEntry(
                    key="a" * 64, response={**match, "mapping": mapping.model_dump(mode="json")}
                ),
            )
        ),
    )
    assert read_warm_candidates(case, cache_path)[1][0].response["operation"] == "act"
    # A post-action interpretation failure can prevent per-edge promotion even
    # though delivery and the final business result both succeeded.
    atomic_write_json(cache_path, VisualMatchDocument())
    recovered = read_warm_candidates(case, cache_path)[1]
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
    library = open_method_library(path, tmp_path / "warm", enabled=True, compiled_only=True)
    bound = library.bind(
        "named-warm", {}, current, {"hbl_no": "HBL-002"}, group_id=compiled.action_groups[0].id
    )
    assert bound is not None
    assert bound["steps"][0]["point"] == [200, 300]
    assert bound["steps"][0]["source"] == "hbl_no"
    assert compiled.action_groups[0].targets[0].anchor_texts == []


@pytest.mark.parametrize("skip_logged", [False, True], ids=["undelivered", "already-satisfied"])
def test_warm_candidates_keep_navigation_after_satisfied_input(tmp_path, skip_logged):
    case, cache_path, visual, *_ = create_successful_case(tmp_path)
    atomic_write_json(cache_path, VisualMatchDocument())
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
            read_warm_candidates(case, tmp_path / "visual-cache.json")
    else:
        from brain.demo_learning.warm_methods import assembly_prompt

        source_data, loaded = read_warm_candidates(case, tmp_path / "visual-cache.json")
        assert len(loaded) == 1
        assert [step["action"] for step in loaded[0].response["steps"]] == ["double_click"]
        assert loaded[0].satisfied_actions[0]["source"] == "hbl_no"
        assert loaded[0].observed_transitions[0]["observed_effect"] == "changed"
        assert '"observed_effect": "changed"' in assembly_prompt(source_data, loaded)


def test_critic_graph_keeps_execution_order_and_excludes_wholly_skipped_plan(tmp_path):
    from brain.demo_learning.warm_methods import assembly_prompt

    case, cache_path, _, _, fill, _ = create_successful_case(tmp_path)
    click = response(target=91, intent="save", point=(700, 550), taught="save")
    header = json.loads((case / "run_log.jsonl").read_text(encoding="utf-8").splitlines()[0])
    events = [header]

    def proposal(plan):
        events.append(
            {
                "kind": "select",
                "payload": {"check": "screen_plan_proposed", "obs_id": "obs-1", "response": plan},
            }
        )

    def action(action_id):
        events.append(
            {"kind": "act", "payload": {"stage": "requested", "action": {"action_id": action_id}}}
        )

    proposal(fill)
    action("a1")
    proposal(fill)  # Same frame, but this whole plan is skipped.
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
    proposal(click)
    action("a2")
    # Reviews arrive after the next proposal, as in the combined observer/planner.
    for action_id, intent, source_key in (("a1", "fill", "hbl_no"), ("a2", "save", None)):
        events.append(
            {
                "kind": "verify",
                "payload": {
                    "transition": {
                        "action_id": action_id,
                        "before_obs_id": "obs-1",
                        "after_obs_id": None,
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
    source_data, loaded = read_warm_candidates(case, cache_path)
    assert len(loaded) == 2
    assert loaded[0].trace_index < loaded[1].trace_index
    assert [c.observed_transitions[0]["action_id"] for c in loaded] == ["a1", "a2"]
    assert len(source_data["nonexecuted_plans"]) == 1
    assert source_data["nonexecuted_plans"][0]["already_satisfied"][0]["source"] == "hbl_no"
    prompt = assembly_prompt(source_data, loaded)
    assert '"previous_candidate_id": "c000"' in prompt
    method = compile_warm_method(source_data, loaded, draft(), method_id="critic")
    assert len({g.trace_index for g in method.action_groups}) == 2
