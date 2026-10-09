"""Generic task contracts do not encode one ERP's fields or screen route."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_brain import FakeRelay, ScriptedModel, decision

from brain.graph.engine import Brain
from brain.graph.execution import GuardStop
from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.graph.schemas import DecisionResponse
from brain.input_binding import normalize_action_source, resolve_input_key
from brain.observe import describe
from brain.observe.values import values_for
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings
from brain.task_instruction import TaskInputRecord, TaskInstruction


def test_shipment_task_declares_duplicate_identity_input():
    task = TaskInstruction.model_validate_json(
        (Path(__file__).resolve().parents[1] / "examples/shipment-task.json").read_bytes()
    )

    assert task.identity_input_key == "hbl_no"


def test_user_taught_actions_preserve_ui_labels_without_coordinates():
    task = TaskInstruction.model_validate(
        {
            "name": "Enter invoice",
            "objective": "Enter and save an invoice.",
            "identity_input_key": "invoice.number",
            "completion_conditions": ["The saved invoice is visible."],
            "procedure": [
                {
                    "id": "open-extra",
                    "instruction": "Press Additional information and enter the amount.",
                    "completion": "The amount is visible.",
                    "actions": [
                        {"verb": "activate", "target": "Additional information tab"},
                        {
                            "verb": "fill",
                            "target": "Amount field",
                            "input_key": "amount",
                        },
                    ],
                }
            ],
        }
    )

    actions = task.procedure[0].actions
    assert task.identity_input_key == "invoice.number"
    assert [(item.verb, item.target, item.input_key) for item in actions] == [
        ("activate", "Additional information tab", None),
        ("fill", "Amount field", "amount"),
    ]
    with pytest.raises(ValidationError):
        TaskInstruction.model_validate(
            {
                "name": "Unsafe route",
                "objective": "Use a stored point.",
                "completion_conditions": ["Done"],
                "procedure": [
                    {
                        "id": "click",
                        "instruction": "Click the control.",
                        "completion": "The next screen is visible.",
                        "actions": [{"verb": "activate", "target": "Next", "point": [10, 20]}],
                    }
                ],
            }
        )


def test_task_record_accepts_arbitrary_business_keys():
    record = TaskInputRecord(
        case_id="invoice-17",
        inputs={"invoice.number": "INV-17", "vendor_code": "V-9", "amount": "1250"},
    )

    assert values_for(record) == record.inputs


def test_task_record_rejects_credential_namespace():
    with pytest.raises(ValidationError, match="task_input_key_invalid"):
        TaskInputRecord(case_id="unsafe", inputs={"credential.password": "secret"})


def test_input_key_vocabulary_is_normalized_at_one_boundary():
    inputs = {"invoice.number": "INV-17", "vendor_code": "V-9"}

    assert resolve_input_key("invoice.number", inputs) == "invoice.number"
    assert resolve_input_key("inputs.invoice.number", inputs) == "invoice.number"
    assert resolve_input_key("record.invoice.number", inputs) == "invoice.number"
    assert resolve_input_key("INV-17", inputs) == "invoice.number"
    assert resolve_input_key("unknown", inputs) is None
    assert (
        normalize_action_source(intent="navigate", candidate="invoice.number", inputs=inputs)
        is None
    )


def test_task_policy_keeps_runtime_keys_reusable_across_screens():
    policy = DecisionPolicy(
        ExecutionFacts(
            phase="task",
            field_names=("invoice.number", "vendor_code"),
            verified_field_names=("vendor_code",),
        )
    )
    allowed = policy.as_prompt()["allowed_next_choices"]
    fill = next(choice for choice in allowed if choice["intent"] == "fill")

    assert fill["action"][0]["source"] == ["invoice.number", "vendor_code"]
    assert policy.as_prompt()["remaining_fields"] == ["invoice.number"]
    assert policy.allows(
        DecisionResponse.model_validate(
            {
                "decision": {
                    "screen_kind": "dialog",
                    "operation": "act",
                    "intent": "fill",
                    "subgoal": "Enter the currently visible invoice number",
                    "reason": "The current dialog exposes that task input",
                    "action": {"type": "set_text", "ref": "field-1", "source": "invoice.number"},
                    "evidence": [],
                }
            }
        ).to_decision()
    )


def test_task_save_requires_all_inputs_evidenced_and_is_issued_once():
    fields = ("invoice.number", "vendor_code")
    save = DecisionResponse.model_validate(
        {
            "decision": {
                "screen_kind": "editor",
                "operation": "act",
                "intent": "save",
                "subgoal": "Save the completed invoice",
                "reason": "All supplied inputs are visible and match",
                "action": {"type": "click", "ref": "save-button"},
                "evidence": [],
            }
        }
    ).to_decision()
    unfinished = DecisionPolicy(ExecutionFacts(phase="task", field_names=fields))
    ready = DecisionPolicy(
        ExecutionFacts(
            phase="task",
            field_names=fields,
            verified_field_names=fields,
        )
    )
    issued = DecisionPolicy(replace(ready.facts, save_issued=True))

    assert not unfinished.allows(save)
    assert ready.allows(save)
    assert not issued.allows(save)


def test_generic_task_policy_uses_evidence_ledger_to_authorize_save(tmp_path):
    record = TaskInputRecord(
        case_id="invoice-save",
        inputs={"invoice.number": "INV-17", "vendor_code": "V-9"},
    )
    relay = FakeRelay(record)
    relay.mode = "editor"
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="generic-task-test")
    brain = Brain(
        record=record,
        goal="Save the supplied invoice.",
        relay=relay,
        model=ScriptedModel(log, []),
        settings=BrainSettings(visual_model_only_review=True),
        case_dir=tmp_path,
        run_log=log,
    )
    brain.phase = "task"
    brain.task_instruction = TaskInstruction(
        name="Save invoice",
        objective="Save the supplied invoice.",
        completion_conditions=["The saved invoice is visible."],
    )
    brain.observation = relay.observe()
    save = DecisionResponse.model_validate(
        {
            "decision": decision(
                intent="save",
                kind="editor",
                action={"type": "click", "ref": "e7"},
            )
        }
    ).to_decision()

    with pytest.raises(GuardStop, match="action_not_allowed_by_state"):
        brain._guard_action(save)

    brain.verified = {field: "current-model-evidence" for field in brain.values}
    assert brain._decision_policy().allows(save)
    brain._guard_action(save)


def test_task_completion_is_tied_to_user_condition_not_all_input_fields():
    policy = DecisionPolicy(
        ExecutionFacts(phase="task", field_names=("invoice.number", "vendor_code"))
    )
    response = policy.response_schema().model_validate(
        {
            "decision": {
                "screen_kind": "dialog",
                "operation": "complete",
                "intent": "verify",
                "subgoal": "Confirm completion",
                "reason": "The visible success message satisfies the user condition",
                "action": None,
                "evidence": [],
                "completion_evidence": [{"condition_index": 0, "ref": "success-message"}],
            }
        }
    )

    assert response.to_decision().completion_evidence[0].condition_index == 0


def test_task_instruction_contains_business_intent_without_ui_steps():
    instruction = TaskInstruction(
        name="Invoice registration",
        objective="Register the supplied invoice in the current business system.",
        requirements=["Use the supplied invoice and vendor values."],
        completion_conditions=["A visible result confirms that the invoice was saved."],
        constraints=["Do not approve payments."],
    )

    assert not hasattr(instruction, "steps")
    assert not hasattr(instruction, "screens")


def test_generic_search_does_not_require_or_mutate_legacy_query_state(tmp_path):
    record = TaskInputRecord(case_id="invoice-17", inputs={"invoice.number": "INV-17"})
    relay = FakeRelay(record)
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="generic-task-test")
    brain = Brain(
        record=record,
        goal="Find the supplied invoice in the current business system.",
        relay=relay,
        model=ScriptedModel(log, []),
        settings=BrainSettings(),
        case_dir=tmp_path,
        run_log=log,
    )
    brain.phase = "task"
    brain.task_instruction = TaskInstruction(
        name="Find invoice",
        objective="Find the supplied invoice in the current business system.",
        requirements=["Use the supplied invoice number."],
        completion_conditions=["The matching invoice is visible."],
    )
    brain.observation = relay.observe()
    search = DecisionResponse.model_validate(
        {
            "decision": decision(
                intent="search",
                action={"type": "click", "ref": "e2"},
            )
        }
    ).to_decision()

    brain._guard_action(search)
    brain.decision = search
    from brain.graph.execution import ActionPlan

    assert brain._execute_plan(ActionPlan.single(search)) == "observation"
    assert brain.pending is not None

    assert not brain.query_value_verified
    assert not brain.query_submitted
    assert brain.query_descriptor is None


def test_model_only_review_accepts_model_bound_completion_without_value_recheck(tmp_path):
    record = TaskInputRecord(case_id="invoice-17", inputs={"invoice.number": "INV-17"})
    relay = FakeRelay(record)
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="generic-task-test")
    brain = Brain(
        record=record,
        goal="Save the supplied invoice.",
        relay=relay,
        model=ScriptedModel(log, []),
        settings=BrainSettings(visual_model_only_review=True),
        case_dir=tmp_path,
        run_log=log,
    )
    brain.phase = "task"
    brain.task_instruction = TaskInstruction(
        name="Save invoice",
        objective="Save the supplied invoice.",
        completion_conditions=["A visible result confirms that the invoice was saved."],
    )
    brain.observation = relay.observe()
    complete = DecisionResponse.model_validate(
        {
            "decision": {
                "screen_kind": "query",
                "operation": "complete",
                "intent": "verify",
                "subgoal": "Confirm completion",
                "reason": "The screen model bound current visible evidence.",
                "action": None,
                "evidence": [{"field": "invoice.number", "ref": "e1"}],
                "completion_evidence": [{"condition_index": 0, "ref": "e2"}],
            }
        }
    ).to_decision()

    assert brain._check_complete(complete) == "finish"
    assert brain.result.status == "success"
    event = next(
        event
        for event in reversed(brain.log.read_all())
        if event.payload.get("check") == "task_completion_fields"
    )
    assert event.payload["reviewer"] == "screen_model"


def test_model_owned_generic_evidence_is_not_invalidated_by_later_screens(tmp_path):
    record = TaskInputRecord(case_id="invoice-17", inputs={"invoice.number": "INV-17"})
    relay = FakeRelay(record)
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="generic-task-test")
    brain = Brain(
        record=record,
        goal="Save the supplied invoice.",
        relay=relay,
        model=ScriptedModel(log, []),
        settings=BrainSettings(visual_model_only_review=True),
        case_dir=tmp_path,
        run_log=log,
    )
    brain.phase = "task"
    brain.task_instruction = TaskInstruction(
        name="Save invoice",
        objective="Save the supplied invoice.",
        completion_conditions=["A visible result confirms that the invoice was saved."],
    )
    brain._invalidate_editor_evidence = lambda: pytest.fail(
        "generic model-owned evidence must survive a screen change"
    )

    brain._observe()
    passive = DecisionResponse.model_validate(
        {
            "decision": decision(
                intent="navigate",
                action={"type": "click", "ref": "e2"},
            )
        }
    ).to_decision()
    brain._collect_editor_evidence(passive)


def test_model_evidence_surface_has_one_active_field_owner(tmp_path):
    from brain.graph.transitions import EvidenceLedger

    ledger = EvidenceLedger()
    descriptor = {
        "role": "Edit",
        "name": "Reference",
        "label": "Reference",
        "context": [{"role": "Group", "name": "Details"}],
    }

    assert ledger.record("first", descriptor, "obs-1") == ()
    assert ledger.owner(descriptor) == "first"
    assert ledger.record("second", descriptor, "obs-2") == ("first",)
    assert ledger.owner(descriptor) == "second"
    assert ledger.prompt_context() == [
        {"field": "second", "observation_id": "obs-2", "revision": 2}
    ]
    assert [(claim.field, claim.status, claim.reason) for claim in ledger.history] == [
        ("first", "superseded", "surface_reassigned"),
        ("second", "active", None),
    ]


def test_task_fill_cannot_overwrite_surface_owned_by_another_input(tmp_path):
    record = TaskInputRecord(
        case_id="invoice-17",
        inputs={"invoice.number": "INV-17", "vendor_code": "V-9"},
    )
    relay = FakeRelay(record)
    relay.mode = "editor"
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="surface-owner-test")
    brain = Brain(
        record=record,
        goal="Save the supplied invoice.",
        relay=relay,
        model=ScriptedModel(log, []),
        settings=BrainSettings(visual_model_only_review=True),
        case_dir=tmp_path,
        run_log=log,
    )
    brain.phase = "task"
    brain.task_instruction = TaskInstruction(
        name="Save invoice",
        objective="Save the supplied invoice.",
        completion_conditions=["The saved invoice is visible."],
    )
    brain.observation = relay.observe()
    target = next(item for item in brain.observation.elements if item.role == "Edit")
    brain.evidence_ledger.record(
        "invoice.number",
        describe(target, brain.observation.elements),
        str(brain.observation.obs_id),
    )
    conflicting = DecisionResponse.model_validate(
        {
            "decision": decision(
                intent="fill",
                kind="editor",
                action={"type": "set_text", "ref": target.ref, "source": "vendor_code"},
            )
        }
    ).to_decision()

    with pytest.raises(GuardStop, match="input_surface_claimed_by_other_field"):
        brain._guard_action(conflicting)
    assert brain._last_surface_conflict == {
        "surface_owner": "invoice.number",
        "requested_source": "vendor_code",
        "target_ref": target.ref,
    }


def test_surface_conflict_during_plan_preflight_reobserves_for_recovery(tmp_path):
    record = TaskInputRecord(
        case_id="invoice-18",
        inputs={"invoice.number": "INV-18", "vendor_code": "V-10"},
    )
    relay = FakeRelay(record)
    relay.mode = "editor"
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="surface-recovery-test")
    brain = Brain(
        record=record,
        goal="Save the supplied invoice.",
        relay=relay,
        model=ScriptedModel(log, []),
        settings=BrainSettings(visual_model_only_review=True),
        case_dir=tmp_path,
        run_log=log,
    )
    brain.phase = "task"
    brain.task_instruction = TaskInstruction(
        name="Save invoice",
        objective="Save the supplied invoice.",
        completion_conditions=["The saved invoice is visible."],
    )
    brain.observation = relay.observe()
    target = next(item for item in brain.observation.elements if item.role == "Edit")
    brain.evidence_ledger.record(
        "invoice.number",
        describe(target, brain.observation.elements),
        str(brain.observation.obs_id),
    )
    brain.decision = DecisionResponse.model_validate(
        {
            "decision": decision(
                intent="fill",
                kind="editor",
                action={"type": "set_text", "ref": target.ref, "source": "vendor_code"},
            )
        }
    ).to_decision()
    brain.execution_authorized = True

    assert brain._act_node() == "observation"
    assert brain.result is None
    assert brain.feedback[-1]["reason"] == "input_surface_claimed_by_other_field"
    assert "key/navigation/scroll" in brain.feedback[-1]["required_response"]
