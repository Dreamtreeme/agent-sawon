"""A recorded method is learned once, then bound to another case's current targets."""

import json
from decimal import Decimal

import pytest

from brain.cache.methods import MethodLibrary
from brain.demo_learning.methods import (
    MethodMeaning,
    learn_method,
    learning_prompt,
    method_from_draft,
    read_method_trace,
)
from brain.runtime.storage import RunLog, atomic_write_json
from models.base import request_fingerprint
from models.replay import ReplayClient, ReplayEntry


def source_trace():
    return {
        "source_case": "recorded-case",
        "obs_id": "before",
        "task": {"objective": "Enter the requested record."},
        "screen": {
            "image_size": [800, 600],
            "ocr": [{"id": 4, "text": "Reference", "bbox": [10, 40, 70, 20]}],
            "markers": [{"id": 10, "source": "omniparser", "bbox": [90, 38, 150, 25]}],
        },
        "response": {
            "screen_kind": "editor",
            "operation": "act",
            "controls": [{"id": 10, "role": "edit", "label_ids": [4], "text_ids": []}],
            "steps": [{"target": 10, "action": "fill", "source": "ref", "intent": "fill"}],
            "expected_result": "The requested reference is entered.",
        },
        "transitions": [
            {
                "seq": 5,
                "action_id": "action-one",
                "before_obs_id": "before",
                "intent": "fill",
                "source": "ref",
                "delivery": "delivered",
                "observed_effect": "unchecked",
            }
        ],
        "after": {
            "obs_id": "after",
            "screen": {
                "image_size": [800, 600],
                "ocr": [{"id": 7, "text": "OLD-REFERENCE-A", "bbox": [90, 38, 140, 25]}],
                "markers": [],
            },
            "response": {"evidence": [{"field": "ref", "target": 8}]},
        },
        "run_result": {"status": "stopped", "reason": "later_step_failed"},
    }


def test_learned_method_rebinds_new_source_and_geometry_without_another_call(tmp_path):
    trace = source_trace()
    draft = MethodMeaning(
        purpose="Enter a record reference in its labeled field",
        slots=[{"name": "reference", "description": "Record identifier", "source_key": "ref"}],
        context_anchors=[],
        expected_result="The requested identifier appears in the reference field.",
        observed_result="The next screen shows the entered reference; a later step stopped.",
    )
    log = RunLog(tmp_path / "learning", case_id="learning", agent_id="test")
    model = ReplayClient(
        [
            ReplayEntry(
                request_sha256=request_fingerprint(learning_prompt(trace), MethodMeaning, None),
                response_json=draft.model_dump_json(),
            )
        ],
        run_log=log,
    )
    path = tmp_path / "methods.json"
    method = learn_method(
        trace,
        model=model,
        log=log,
        method_id="reference-entry",
        output=path,
        max_cost_usd=Decimal(0),
    )
    current = {
        "ocr": [{"id": 80, "text": "Reference", "bbox": [70, 120, 70, 20]}],
        "markers": [{"id": 90, "source": "omniparser", "bbox": [150, 118, 150, 25]}],
    }
    bound = MethodLibrary(path, enabled=True).bind(
        method.id, {"reference": "new_key"}, current, {"new_key": "NEW-REFERENCE-B"}
    )
    assert bound["steps"][0]["target"] == 90
    assert bound["steps"][0]["source"] == "new_key"
    assert method.source_transitions == [
        "recorded-case/run_log.jsonl:5",
        str((log.case_dir / "method-draft.json").resolve()),
    ]
    assert "OLD-REFERENCE-A" not in path.read_text(encoding="utf-8")
    assert model.remaining == 0
    assert len([event for event in log.read_all() if event.kind == "model_call"]) == 1
    # Rebuilding older extraction output neither calls the provider nor loses its receipt.
    prior_draft = {**draft.model_dump(), "expected_result": "x" * 277}
    rebuilt = method_from_draft(
        trace,
        prior_draft,
        method_id=method.id,
        source_receipt=method.source_transitions[-1],
    )
    assert len(rebuilt.action_groups[0].expected_result) == 240
    assert rebuilt.source_transitions == method.source_transitions
    assert len([event for event in log.read_all() if event.kind == "model_call"]) == 1


def test_trace_joins_actual_delivery_and_next_observation_without_claiming_success(tmp_path):
    trace = source_trace()
    for obs_id, screen in [("before", trace["screen"]), ("after", trace["after"]["screen"])]:
        folder = tmp_path / "visual" / obs_id
        atomic_write_json(folder / "ocr.json", {"blocks": screen["ocr"]})
        atomic_write_json(
            folder / "markers.json",
            {"markers": screen["markers"], "image_size": screen["image_size"]},
        )
    atomic_write_json(tmp_path / "visual/before/screen-match.json", trace["response"])
    atomic_write_json(tmp_path / "task-instruction.json", trace["task"])
    atomic_write_json(tmp_path / "result.json", trace["run_result"])
    events = [
        {
            "seq": 4,
            "kind": "act",
            "payload": {"stage": "returned", "action_id": "action-one", "ok": True},
        },
        {"seq": 6, "kind": "observe", "payload": {"stage": "capture_timing"}},
        {"seq": 7, "kind": "observe", "payload": {"obs_id": "after"}},
        {"seq": 8, "kind": "verify", "payload": {"transition": trace["transitions"][0]}},
        {"seq": 9, "kind": "observe", "payload": {"obs_id": "later-dialog"}},
    ]
    (tmp_path / "run_log.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )
    loaded = read_method_trace(tmp_path)
    assert loaded["obs_id"] == "before"
    assert loaded["after"]["obs_id"] == "after"
    assert loaded["transitions"][0]["observed_effect"] == "unchecked"
    assert loaded["run_result"]["status"] == "stopped"
    assert "OLD-REFERENCE-A" in learning_prompt(loaded)
    # One delivered action cannot justify an unexecuted suffix, even when its slot repeats.
    partial = {**trace["response"], "steps": trace["response"]["steps"] * 2}
    atomic_write_json(tmp_path / "visual/before/screen-match.json", partial)
    events.append({"seq": 10, "kind": "verify", "payload": {"transition": trace["transitions"][0]}})
    (tmp_path / "run_log.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="No recorded noncredential action batch"):
        read_method_trace(tmp_path)
