"""Synthetic 093 replays with fake vision/model; no desktop or provider calls."""

import json
from pathlib import Path

from sawon_interfaces import ActionResult
from test_brain import record as record
from test_screen_decision import named_response, setup_brain

from brain.graph.schemas import Choice, Decision
from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES
from brain.task_instruction import TaskInstruction


def taught_task():
    return TaskInstruction.model_validate_json(
        (
            Path(__file__).resolve().parents[1] / "examples/shipment-user-taught-task.json"
        ).read_bytes()
    )


def test_synthetic_093_detail_transition_replans_from_committed_evidence(tmp_path, record):
    # The first fake reproduces the observed wrong Close plan, not model intelligence.
    # The second tests what context the real selection path receives after commitment.
    close = named_response(
        screen_kind="editor",
        taught_step_id="requery-saved-record",
        procedure_step="task-action",
        fields={
            "Reference": {
                "role": "edit",
                "bbox": [100, 15, 150, 30],
                "value": record.fields.hbl_no.value,
            },
            "Close": {"role": "button", "bbox": [270, 15, 90, 30]},
        },
        steps=[{"target": "Close", "action": "click", "intent": "navigate", "point": [310, 28]}],
    )
    review = named_response(
        screen_kind="editor",
        taught_step_id="verify-requeried-record",
        fields={"More": {"role": "tabitem", "bbox": [270, 60, 90, 30]}},
        assignments={},
        procedure_step="task-action",
        steps=[{"target": "More", "action": "click", "intent": "select_tab", "point": [310, 75]}],
    )
    brain, model, relay, ocr, detector, _ = setup_brain(
        tmp_path, record, [close, review], task_instruction=taught_task()
    )
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    brain.save_issued = True
    brain.active_taught_step = "requery-saved-record"
    brain.saved_detail_transition_pending = True
    brain.saved_detail_transition_step = "requery-saved-record"
    brain._observe()
    assert brain._select_node() == "review"
    assert brain.decision.intent == "select_tab", "pre-commit Close must not reach execution"
    assert len(model.prompts) == 2
    state = json.loads(model.prompts[-1][len(CONTEXT_MAPPING_RULES) :])
    assert state["notebook"]["verified_inputs"] == ["hbl_no"]
    assert state["notebook"]["workflow_state"]["post_save_detail_review"]
    assert not state["notebook"]["workflow_state"]["saved_result_open_pending"]
    assert state["taught_procedure_progress"]["focused_step_id"] == "verify-requeried-record"
    assert ocr.calls == detector.calls == 1, "reuse the same capture and interpretation"
    sent = []

    def fake_act(action):
        sent.append(action.root)
        return ActionResult(
            action_id=action.root.action_id,
            ok=True,
            duration_ms=0,
            post_obs_id=relay.current.obs_id,
        )

    relay.act = fake_act
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert [action.target.image_point for action in sent] == [(310, 75)]


def test_synthetic_093_reentry_consumes_pending_without_erasing_review(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=taught_task())
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    brain.save_issued = brain.model_saved_detail_review = True
    brain.saved_detail_transition_pending = True
    brain.saved_detail_transition_step = "requery-saved-record"
    brain.post_save_verified = {"hbl_no", "shipper"}
    brain._begin_saved_detail_review("query")
    assert brain.saved_detail_transition_pending, "a query frame does not prove opening"
    assert brain._begin_saved_detail_review("editor") is False, (
        "reentry needs no extra plan refresh"
    )
    assert not brain.saved_detail_transition_pending
    assert brain.saved_detail_transition_step is None
    assert brain.post_save_verified == {"hbl_no", "shipper"}


def test_synthetic_completed_step_selection_does_not_rewind_education_focus(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=taught_task())
    brain._complete_taught_procedure_step("requery-saved-record")
    brain._advance_taught_procedure("requery-saved-record")
    assert brain._taught_procedure_progress()["focused_step_id"] == "verify-requeried-record"
    # Uncompleted earlier work remains selectable; this is not an action rejection.
    brain._advance_taught_procedure("enter-visible-record")
    assert brain.active_taught_step == "enter-visible-record"


def test_synthetic_pending_context_preserves_action_and_source_screen(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    decision = Decision(
        screen_kind="query",
        operation="act",
        intent="navigate",
        subgoal="open saved row",
        reason="review saved values",
        action=Choice(type="double_click", ref="old-row"),
    )
    brain.pending = {"decision": decision}
    action = brain._execution_context(anticipate=True)["pending_actions"][0]
    assert action["screen_kind"] == "query"
    assert action["action"] == "double_click"
    assert action["subgoal"] == "open saved row"
    assert "old-row" not in json.dumps(action), "ephemeral refs are not current targets"
