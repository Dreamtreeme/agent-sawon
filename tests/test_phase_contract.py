"""The model sees instructions and repair metadata for the current checkpoint."""

import json

import pytest
from pydantic import ValidationError
from test_brain import FakeRelay, ScriptedModel, run_brain, success_steps
from test_brain import record as record

from brain.graph.engine import Brain
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings


@pytest.mark.parametrize("step", ["query_input", "search", "final_row_evidence"])
def test_final_request_only_explains_current_checkpoint(tmp_path, record, step):
    relay = FakeRelay(record, duplicate=step == "final_row_evidence")
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="phase-test")
    brain = Brain(
        record=record,
        goal="Enter and verify",
        relay=relay,
        model=ScriptedModel(log, []),
        settings=BrainSettings(),
        case_dir=tmp_path,
        run_log=log,
    )
    brain.phase = "final_query"
    brain.save_issued = brain.duplicate_clear = True
    brain.query_value_verified = step != "query_input"
    brain.query_submitted = step == "final_row_evidence"
    brain.observation = relay.observe()
    brain.feedback = [
        {"phase": "enter", "required_response": "obsolete entry repair"},
        {
            "phase": "final_query",
            "next_required_step": "query_input",
            "required_response": "obsolete input repair",
        },
    ]
    prompt = brain._decision_prompt()
    task = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
    assert task["next_required_step"] == step
    assert "obsolete entry repair" not in prompt
    if step != "query_input":
        assert "obsolete input repair" not in prompt
    if step == "final_row_evidence":
        brain._decision_policy().response_schema().model_validate(
            {"decision": success_steps(record)[-1]}
        )
        assert "operation=complete" in task["phase_instruction"]
    else:
        with pytest.raises(ValidationError):
            brain._decision_policy().response_schema().model_validate(
                {"decision": success_steps(record)[-1]}
            )
        assert "operation=complete" not in prompt
        assert f"intent={step}" in task["phase_instruction"]
    assert not log.read_all()


def test_pending_invalid_operation_returns_field_diagnostics_and_current_repair(tmp_path, record):
    steps = success_steps(record)
    relay = FakeRelay(record)
    invalid = {**steps[-1], "operation": "stop", "intent": "verify"}

    def correct_search(prompt):
        task = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        repair = next(
            item for item in task["feedback"] if item.get("reason") == "final_query_required"
        )
        assert task["next_required_step"] == "search"
        assert "intent=search" in repair["required_response"]
        assert "operation=complete" not in repair["required_response"]
        assert repair["validation_errors"]
        assert any(item["loc"] for item in repair["validation_errors"])
        assert all(set(item) <= {"type", "loc", "rule"} for item in repair["validation_errors"])
        return steps[-2]

    result, brain, _, _ = run_brain(
        tmp_path, record, [*steps[:-2], invalid, correct_search, steps[-1]], relay=relay
    )
    assert result.status == "success", result
    assert len(relay.actions) == len(steps) - 2
    errors = [
        event
        for event in brain.log.read_all()
        if event.kind == "model_call" and event.payload.get("error_type") == "ValidationError"
    ]
    assert len(errors) == 1
    assert errors[0].payload["validation_errors"]
