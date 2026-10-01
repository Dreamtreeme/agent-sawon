"""Missing current cells must lead to grounded recovery, never invented proof."""

import json

from test_brain import FakeRelay, ScriptedModel, decision, element, run_brain, success_steps
from test_brain import record as record

from brain.graph.engine import Brain, field_matches, values_for
from brain.graph.review import review_final
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings


class HiddenFinalRelay(FakeRelay):
    expanded = False

    def observe(self):
        observation = super().observe()
        if self.saved and not self.expanded:
            observation.elements = [e for e in observation.elements if e.ref not in {"e40", "e50"}]
            observation.elements.append(element("e9", "Button", name="Expand view", parent="e0"))
        return observation

    def act(self, action):
        result = super().act(action)
        if action.root.type == "click" and action.root.target.ref == "e9":
            self.expanded = True
        return result


def test_query_input_value_never_substitutes_for_hidden_row_identifier(record):
    relay = HiddenFinalRelay(record, duplicate=True)
    relay.query = record.fields.hbl_no.value
    observation = relay.observe()
    result = review_final(observation, values_for(record), field_matches)
    assert result.status == "needs_more"
    assert result.reason == "row_identifier_not_visible"
    assert result.row_ref is None
    assert set(result.missing_fields) == {"hbl_no", "pod"}
    relay.expanded = True
    fresh = relay.observe()
    assert review_final(fresh, values_for(record), field_matches).status == "ready"


def test_missing_cells_are_removed_from_completion_schema_then_recovered(tmp_path, record):
    relay = HiddenFinalRelay(record)

    def expand(prompt):
        task = json.loads(prompt.split("TRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert task["evidence_review"]["status"] == "needs_more"
        assert not task["execution_policy"]["verified_facts"]["final_evidence_ready"]
        assert "complete" not in [
            choice["operation"] for choice in task["execution_policy"]["allowed_next_choices"]
        ]
        assert "operation=complete" not in task["phase_instruction"]
        return decision(action={"type": "click", "ref": "e9"})

    steps = success_steps(record)
    result, brain, _, _ = run_brain(tmp_path, record, [*steps[:-1], expand, steps[-1]], relay=relay)
    assert result.status == "success", result
    assert relay.expanded
    reviews = [
        e.payload
        for e in brain.log.read_all()
        if e.payload.get("check") == "current_evidence_review"
    ]
    assert reviews[0]["status"] == "needs_more"
    assert reviews[-1]["status"] == "ready"
    assert not any(
        e.payload.get("reason") == "final_query_not_one_matching_row" for e in brain.log.read_all()
    )


def test_configured_recovery_uses_separately_measured_model_on_missing_evidence(tmp_path, record):
    relay = HiddenFinalRelay(record)
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="recovery-test")
    steps = success_steps(record)
    lite = ScriptedModel(log, steps)
    stronger = ScriptedModel(log, [decision(action={"type": "click", "ref": "e9"})])
    stronger.model = "recovery-fixture"
    brain = Brain(
        record=record,
        goal="Save for approval",
        relay=relay,
        model=lite,
        recovery_model=stronger,
        settings=BrainSettings(recovery_enabled=True),
        case_dir=tmp_path,
        run_log=log,
    )
    assert brain.run().status == "success"
    calls = [e for e in log.read_all() if e.kind == "model_call"]
    assert len([e for e in calls if e.payload["model"] == "recovery-fixture"]) == 1
    assert all(e.cost.usd is not None for e in calls)


def test_hidden_fields_cannot_be_completed_by_model_repeatedly_claiming_success(tmp_path, record):
    steps = success_steps(record)
    result, brain, relay, model = run_brain(
        tmp_path,
        record,
        [*steps, steps[-1]],
        relay=HiddenFinalRelay(record),
    )
    assert result.status == "stopped"
    assert result.reason == "repeated_subgoal_failure:final_query:decision"
    assert result.steps == len(steps) + 1
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(steps) - 2
    assert not relay.expanded
    events = brain.log.read_all()
    assert [
        e.payload["reason"] for e in events if e.kind == "verify" and e.payload.get("ok") is False
    ] == ["selection_error:ValidationError"] * 2
    reviews = [e.payload for e in events if e.payload.get("check") == "current_evidence_review"]
    assert reviews and all(r["status"] == "needs_more" for r in reviews)
    assert all(set(r["missing_fields"]) == {"hbl_no", "pod"} for r in reviews)
    assert not any(e.payload.get("check") == "final_query_fields" for e in events)
