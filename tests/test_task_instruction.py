"""Generic task contracts do not encode one ERP's fields or screen route."""

import pytest
from pydantic import ValidationError

from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.graph.schemas import DecisionResponse
from brain.observe.values import values_for
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
