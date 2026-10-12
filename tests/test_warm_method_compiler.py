import hashlib
import json

import pytest
from PIL import Image

from brain.cache.methods import MethodDocument, MethodLibrary
from brain.cache.visual_match import VisualMatchDocument, VisualMatchEntry
from brain.demo_learning.warm_methods import (
    WarmCandidate,
    WarmMethodAssembly,
    compile_warm_method,
    read_warm_candidates,
)
from brain.graph.method_runtime import open_method_library
from brain.graph.screen_mapping import ScreenMapping
from sawon_runtime.storage import atomic_write_json


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
    assert all(group.targets[0].screen_relative_point is None for group in method.action_groups)
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


def test_warm_compiler_rejects_missing_input():
    with pytest.raises(ValueError, match="warm_method_missing_sources:hbl_no"):
        compile_warm_method(source(), candidates(), draft(groups=("c001",)), method_id="bad")


def test_successful_case_is_joined_only_to_promoted_cache(tmp_path):
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

    loaded_source, loaded = read_warm_candidates(case, cache_path)

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
