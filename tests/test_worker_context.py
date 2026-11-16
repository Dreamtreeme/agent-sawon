"""Screen mapping receives current execution context and verified facts."""

import json
from copy import deepcopy

import pytest
from test_brain import FakeRelay, element
from test_brain import record as record
from test_screen_decision import fixture, named_response, setup_brain

from brain.graph.memory_retrieval import open_memory_library
from brain.graph.schemas import Choice, Decision
from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES
from brain.graph.transitions import ObservedEffect
from brain.task_instruction import TaskInstruction
from brain.visual.markers import build_markers


@pytest.mark.parametrize(
    "phase,step",
    [
        ("task", "task-action"),
        ("save_result", "inspect-save"),
    ],
)
def test_screen_matching_receives_current_execution_context(tmp_path, record, phase, step):
    blocks, boxes, _, _ = fixture()
    markers = build_markers(blocks, boxes, 400, 200)
    brain, model, *_ = setup_brain(
        tmp_path,
        record,
        [named_response(procedure_step=step, steps=[], operation="stop")],
    )
    brain.phase = phase
    expected = brain._execution_context()
    brain._decide_visual_screen(
        {
            "ocr": [b.model_dump() for b in blocks],
            "markers": [{**m.model_dump(), "contained_ocr_ids": list(m.text_ids)} for m in markers],
        },
        None,
    )
    matched = json.loads(model.prompts[-1][len(CONTEXT_MAPPING_RULES) :])
    assert matched["execution"] == expected
    if phase == "save_result":
        assert "instructions" not in matched["execution"]["save_review"]
        assert matched["execution"]["guidance"].count("SAVE RESULT REVIEW:") == 1
        proof_context = brain._execution_context(
            saved_record_review=brain.saved_evidence.snapshot(brain.values)
        )
        assert "Verify the saved record using CURRENT query-row" in proof_context["guidance"]
        assert proof_context["guidance"].count("SAVE RESULT REVIEW:") == 1


def test_generic_task_context_has_no_legacy_query_input_gate(tmp_path, record):
    task_instruction = TaskInstruction(
        name="Arbitrary business task",
        objective="Use the supplied inputs to complete the user's work.",
        requirements=["Interpret the current application from visible evidence."],
        completion_conditions=["Visible evidence confirms the requested result."],
    )
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)

    context = brain._execution_context()

    assert context["checkpoint"] == "task"
    assert "query_input" not in context
    assert context["remaining_inputs"] == sorted(brain.values)
    assert "remaining_fields" not in context
    assert context["guidance"] == "Use the user task description as the workflow."


def test_synthetic_window_delivery_keeps_target_in_next_model_request(tmp_path, record):
    # Synthetic window switch: real receipt/context functions, fake model and relay.
    from sawon_interfaces import Action, WindowInfo

    brain, model, *_ = setup_brain(
        tmp_path, record, [named_response(fields={}, steps=[], operation="stop")]
    )
    before = FakeRelay(record).observe()
    target = WindowInfo(handle=987, title="Synthetic Mail", process="mail.exe")
    brain.observation = before.model_copy(update={"windows": [before.active_window, target]})
    decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="navigate",
        subgoal="Switch apps",
        reason="Switch apps",
        action=Choice(type="focus_window", handle=target.handle),
    )
    wire = Action.model_validate(
        {
            "type": "focus_window",
            "action_id": "00000000-0000-0000-0000-000000000001",
            "obs_id": str(before.obs_id),
            "handle": target.handle,
            "reason": "navigate",
        }
    )
    brain.pending = brain._accept_dispatched_action(wire, decision, decision.action)
    brain._record_action_verification(
        decision, decision.action, None, True, ObservedEffect.CHANGED, []
    )
    brain._decide_visual_screen({"image_size": [400, 200], "ocr": [], "markers": []}, None)
    state = json.loads(model.prompts[-1][len(CONTEXT_MAPPING_RULES) :])
    delivered = state["execution"]["last_delivered_actions"][0]
    assert delivered["handle"] == target.handle
    assert delivered["target"] == target.title
    assert state["notebook"]["recent_turns"][-1]["target_summary"] == (
        "focus_window:Synthetic Mail"
    )
    assert state["notebook"]["recent_turns"][-1]["observed_effect"] == "changed"
    assert "success" not in state["notebook"]["recent_turns"][-1]
    assert state["notebook"]["scratchpad"]["recorded_facts"] == []


def test_synthetic_save_receipt_keeps_delivery_and_no_change_separate_in_next_request(
    tmp_path, record
):
    # Synthetic receipt, real memory/context path: delivery does not create a saved-value reading.
    from uuid import uuid4

    from sawon_interfaces import Action

    brain, model, *_ = setup_brain(
        tmp_path, record, [named_response(fields={}, steps=[], operation="stop")]
    )
    before = (
        FakeRelay(record)
        .observe()
        .model_copy(update={"elements": [element("e1", "Button", name="Save")]})
    )
    brain.observation = before
    decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="save",
        subgoal="Save",
        reason="Save",
        action=Choice(type="click", ref="e1"),
    )
    wire = Action.model_validate(
        {
            "schema_version": "0.2",
            "type": "click",
            "action_id": str(uuid4()),
            "obs_id": str(before.obs_id),
            "reason": "save",
            "target": {
                "image_point": [10, 10],
                "capture_id": str(uuid4()),
                "evidence": "synthetic",
                "source": "visual",
            },
        }
    )
    brain.pending = brain._accept_dispatched_action(wire, decision, decision.action)
    brain._record_action_verification(
        decision, decision.action, None, None, ObservedEffect.NO_CHANGE, ["save_issued"]
    )
    brain._decide_visual_screen({"image_size": [400, 200], "ocr": [], "markers": []}, None)
    notebook = json.loads(model.prompts[-1][len(CONTEXT_MAPPING_RULES) :])["notebook"]
    assert notebook["workflow_state"]["save_issued"] is True
    assert notebook["scratchpad"]["recorded_facts"] == ["save_issued"]
    assert notebook["recent_turns"][-1]["observed_effect"] == "no_change"
    assert "success" not in notebook["recent_turns"][-1]
    assert notebook["verified_inputs"] == []
    assert "검증 완료" not in json.dumps(notebook, ensure_ascii=False)


def test_synthetic_action_summary_uses_delivered_frame_not_new_frame(tmp_path, record):
    # Synthetic frame change: an old ref must keep its original target meaning.
    brain, *_ = setup_brain(tmp_path, record, [])
    before = (
        FakeRelay(record)
        .observe()
        .model_copy(update={"elements": [element("e1", "Button", name="Original command")]})
    )
    brain.observation = before.model_copy(
        update={"elements": [element("e2", "Button", name="Next screen command")]}
    )
    decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="navigate",
        subgoal="Switch view",
        reason="Switch view",
        action=Choice(type="click", ref="e1"),
    )
    brain.pending = {"before_observation": before, "decision": decision, "choice": decision.action}
    brain._record_action_verification(
        decision, decision.action, None, True, ObservedEffect.CHANGED, []
    )
    assert brain.rolling_context.recent_turns[-1].target_summary == "click:Original command"


def test_pending_external_selection_exposes_only_post_verification_steps(tmp_path, record):
    blocks, boxes, _, meanings = fixture()
    combined = named_response(
        screen_kind="editor",
        procedure_step="open-additional-entry",
        fields={"Additional": {"role": "button", "bbox": [100, 15, 150, 30]}},
        steps=[
            {"target": "Additional", "action": "click", "intent": "select_tab", "point": [175, 30]}
        ],
    )
    brain, model, *_ = setup_brain(tmp_path, record, [combined])
    methods = brain.settings.methods.model_copy(update={"enabled": True})
    brain.settings = brain.settings.model_copy(update={"methods": methods})
    brain.method_library = open_memory_library(
        methods.path,
        brain.case_dir,
        enabled=methods.enabled,
        promotion_path=methods.promotion_path,
        compiled_only=methods.compiled_only,
        max_phash_distance=methods.max_phash_distance,
    )
    brain.phase = "task"
    brain.duplicate_clear = True
    future_plan = "Select this shipper, then recheck previously observed upper fields."
    brain.decision = Decision(
        screen_kind="partner",
        operation="act",
        intent="select_option",
        subgoal=future_plan,
        reason="select partner",
        action=Choice(type="click", ref="e1"),
    )
    before = (
        FakeRelay(record)
        .observe()
        .model_copy(update={"elements": [element("e1", "Button", name="Select shipper")]})
    )
    brain.pending = {
        "decision": brain.decision,
        "choice": brain.decision.action,
        "before_observation": before,
        "effect_source": "shipper",
    }
    brain.feedback.append({"ok": True, "subgoal": future_plan, "check": "action_delivered"})
    brain.memory_retrieval.state.purpose = future_plan
    brain.memory_retrieval.state.history.append(
        {"purpose": future_plan, "status": "delivered", "group_id": "selected-partner"}
    )
    original_feedback = deepcopy(brain.feedback)
    original_history = deepcopy(brain.memory_retrieval.state.history)
    assert brain.memory_retrieval.prompt_context()["recent_executions"] == [
        {"status": "delivered", "group_id": "selected-partner"}
    ]
    assert brain.memory_retrieval.state.purpose == future_plan, "projection preserves the source"
    assert brain.memory_retrieval.state.history == original_history
    markers = build_markers(blocks, boxes, 400, 200)
    matched = brain._decide_visual_screen(
        {
            "image_size": [400, 200],
            "ocr": [block.model_dump() for block in blocks],
            "markers": [
                {**marker.model_dump(), "contained_ocr_ids": list(marker.text_ids)}
                for marker in markers
            ],
        },
        None,
    )

    state = json.loads(model.prompts[-1][len(CONTEXT_MAPPING_RULES) :])
    assert matched.procedure_step == "task-action"
    assert "select_tab" in state["capability"]["actions"]
    assert state["execution"]["last_delivered_actions"] == [
        {
            "intent": "select_option",
            "source": None,
            "screen_kind": "partner",
            "action": "click",
            "target": "Select shipper",
        }
    ]
    assert future_plan not in json.dumps(state), "future work is not a delivered action fact"
    assert brain.decision.subgoal == future_plan
    assert brain.feedback == original_feedback
    assert brain.memory_retrieval.state.history == original_history
    assert state["method_memory"]["recent_executions"] == [
        {"status": "delivered", "group_id": "selected-partner"}
    ]
    assert "shipper" not in brain._observed_inputs()
    assert state["execution"]["last_delivered_actions"][0]["intent"] == "select_option"
    assert "awaiting_visual_result_review" not in state["notebook"]
    assert "pending_actions" not in state["execution"]
    assert "pending_actions" not in state["notebook"]
    assert "pending_verification" not in state["notebook"]
    assert "anticipated_verified_inputs" not in state["notebook"]
    assert "shipper" in state["execution"]["remaining_inputs"]
    assert state["execution"]["conditional"] is True
    assert "shipper" in state["notebook"]["remaining_inputs"]
    assert "shipper" not in state["notebook"]["verified_inputs"]
    assert not {"duplicate_clear", "query_value_verified", "query_submitted"} & set(
        state["notebook"]["workflow_state"]
    )


def test_pending_login_proposes_post_authentication_work_without_resubmitting_credentials(
    tmp_path, record
):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.credentials = {
        "credential.username": "fixture-user",
        "credential.password": "fixture-password",
    }
    brain.decision = Decision(
        screen_kind="login",
        operation="act",
        intent="login",
        subgoal="login",
        reason="submit credentials",
        action=Choice(type="click", ref="e1"),
    )
    brain.pending = {"decision": brain.decision}

    actual = brain._execution_context()
    proposed = brain._execution_context(anticipate=True)

    assert "credential" in actual["allowed_intents"]
    assert proposed["conditional"]
    assert "credential" not in proposed["allowed_intents"]
    assert "login" not in proposed["allowed_intents"]
    assert "Use the user task description" in proposed["guidance"]
    assert brain._decision_policy().facts.available_credential_sources
