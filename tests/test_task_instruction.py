"""Generic task contracts do not encode one ERP's fields or screen route."""

import pytest
from pydantic import ValidationError
from test_brain import FakeRelay, ScriptedModel, decision

from brain.graph.engine import Brain
from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.graph.schemas import DecisionResponse
from brain.input_binding import normalize_action_source, resolve_input_key
from brain.observe.values import values_for
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings
from brain.task_instruction import TaskInputRecord, TaskInstruction


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


def test_task_policy_derives_choices_from_runtime_keys():
    policy = DecisionPolicy(
        ExecutionFacts(
            phase="task",
            field_names=("invoice.number", "vendor_code"),
            verified_field_names=("vendor_code",),
        )
    )
    allowed = policy.as_prompt()["allowed_next_choices"]
    fill = next(choice for choice in allowed if choice["intent"] == "fill")

    assert fill["action"][0]["source"] == ["invoice.number"]
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
    assert brain._dispatch_action() == {"next": "verify"}

    assert not brain.query_value_verified
    assert not brain.query_submitted
    assert brain.query_descriptor is None
