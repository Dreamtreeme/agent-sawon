"""Screen mapping receives current execution context and verified facts."""

import json

import pytest
from test_brain import record as record
from test_screen_decision import fixture, named_response, setup_brain

from brain.graph.schemas import Choice, Decision
from brain.graph.screen_decision import ScreenMatch
from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES
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
    assert "No application route is predefined" in context["guidance"]


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
    brain.phase = "task"
    brain.duplicate_clear = True
    basics = {"hbl_no", "mbl_no", "packages.count", "packages.unit", "pol"}
    brain.verified = {field: f"ref-{field}" for field in basics}
    brain.decision = Decision(
        screen_kind="partner",
        operation="act",
        intent="select_option",
        subgoal="select-shipper",
        reason="select partner",
        action=Choice(type="click", ref="e1"),
    )
    brain.pending = {"decision": brain.decision, "effect_source": "shipper"}
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
            "subgoal": "select-shipper",
        }
    ]
    assert "shipper" not in brain.verified
    assert state["execution"]["last_delivered_actions"][0]["intent"] == "select_option"
    assert "awaiting_visual_result_review" not in state["notebook"]
    assert "pending_actions" not in state["execution"]
    assert "pending_actions" not in state["notebook"]
    assert "pending_verification" not in state["notebook"]
    assert "anticipated_verified_inputs" not in state["notebook"]
    assert "shipper" not in state["execution"]["remaining_inputs"]
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


def test_opening_saved_detail_scopes_request_without_erasing_entry_on_failed_navigation(
    tmp_path, record
):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.save_issued = True
    entry = {field: f"entry-{field}" for field in brain.values}
    brain.verified = dict(entry)
    brain.saved_detail_transition_pending = True
    brain.pending = {
        "decision": Decision(
            screen_kind="query",
            operation="act",
            intent="navigate",
            subgoal="open",
            reason="open saved detail",
            action=Choice(type="double_click", ref="row"),
        ),
    }
    request = brain._screen_decision_request({}, None)
    assert not request.verified_fields
    assert not request.policy.facts.verified_field_names
    assert set(request.execution_contract["remaining_inputs"]) == set(brain.values)
    brain._begin_saved_detail_review(
        ScreenMatch(screen_kind="query", procedure_step="task-action", controls=[])
    )
    assert not brain.model_saved_detail_review
    assert brain.verified == entry
    assert not brain.post_save_verified
