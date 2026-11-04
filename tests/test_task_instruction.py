"""Generic task contracts do not encode one ERP's fields or screen route."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError
from runtime_profile import current_settings
from test_brain import FakeRelay, ScriptedModel, decision

from brain.graph.engine import Brain
from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.graph.schemas import CompletionEvidence, DecisionResponse
from brain.input_binding import normalize_action_source, resolve_input_key
from brain.observe.values import values_for
from brain.task_instruction import TaskInputRecord, TaskInstruction
from sawon_runtime.storage import RunLog


class PassthroughScanner:
    def scan(self, observation):
        return observation


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
    without_annotations = response.model_dump()
    without_annotations["decision"].pop("completion_evidence")
    assert (
        policy.response_schema()
        .model_validate(without_annotations)
        .to_decision()
        .completion_evidence
        == []
    )


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
    from test_visual_workflow import SyntheticScanner, VisualFixture

    relay = VisualFixture(record)
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="generic-task-test")
    brain = Brain(
        record=record,
        goal="Find the supplied invoice in the current business system.",
        relay=relay,
        model=ScriptedModel(log, []),
        settings=current_settings(tmp_path),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    brain.phase = "task"
    brain.task_instruction = TaskInstruction(
        name="Find invoice",
        objective="Find the supplied invoice in the current business system.",
        requirements=["Use the supplied invoice number."],
        completion_conditions=["The matching invoice is visible."],
    )
    brain.observation = SyntheticScanner(relay).scan(relay.observe())
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
        settings=current_settings(tmp_path),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
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

    # The semantic declaration is model-owned; supplied annotations still have
    # to name real current targets and existing user conditions. These are
    # synthetic boundary inputs, not another model/ERP execution.
    from brain.graph.completion import CompletionRetry, evaluate_task_completion
    from brain.graph.execution import GuardStop

    conditions = brain.task_instruction.completion_conditions
    unannotated = complete.model_copy(update={"evidence": [], "completion_evidence": []})
    reviewed = evaluate_task_completion(
        brain.observation, unannotated, brain.values, conditions, model_only_review=True
    )
    assert reviewed.payload["conditions"] == {}, "do not invent condition indices or targets"
    with pytest.raises(CompletionRetry, match="task_completion_evidence_required"):
        evaluate_task_completion(
            brain.observation, unannotated, brain.values, conditions, model_only_review=False
        )
    for annotation, reason in (
        (
            CompletionEvidence(condition_index=1, ref="e2"),
            "task_completion_condition_unknown",
        ),
        (
            CompletionEvidence(condition_index=0, ref="missing"),
            "task_completion_evidence_missing",
        ),
    ):
        with pytest.raises(GuardStop, match=reason):
            evaluate_task_completion(
                brain.observation,
                unannotated.model_copy(update={"completion_evidence": [annotation]}),
                brain.values,
                conditions,
                model_only_review=True,
            )
