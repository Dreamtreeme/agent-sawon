"""A rejected text target can be reselected before dispatch, under fixed bounds."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_brain import (
    FakeRelay,
    ScriptedModel,
    decision,
    run_brain,
    success_steps,
    text_choice,
)
from test_brain import record as record

from brain.graph.engine import GuardStop
from brain.settings import BrainSettings


def bad_input(*, kind="query", source="hbl_no", ref="e2"):
    return decision(
        kind=kind,
        intent="query_input" if kind == "query" else "fill",
        action=text_choice(source, ref),
    )


def repairs(brain):
    return [
        event.payload
        for event in brain.log.read_all()
        if event.kind == "verify" and event.payload.get("check") == "input_target_reselection"
    ]


def test_rejected_query_and_editor_targets_recover_in_one_full_workflow(tmp_path, record):
    steps = success_steps(record)
    steps.insert(0, bad_input())
    steps.insert(5, bad_input(kind="editor", ref="e7"))
    result, brain, relay, model = run_brain(
        tmp_path,
        record,
        steps,
        settings=BrainSettings(max_input_target_retries=2),
    )
    assert result.status == "success", result
    assert relay.inputs == relay.values
    assert brain.input_target_retries == 2
    assert len(repairs(brain)) == 2
    assert all(item["action_dispatched"] is False for item in repairs(brain))
    assert len(model.prompts) == 22  # Original workflow plus two measured corrections.
    assert len([e for e in brain.log.read_all() if e.kind == "model_call"]) == 22
    assert len(relay.actions) == 17  # Rejected targets never reached the relay.


def test_retry_uses_new_observation_refs_and_explicit_non_dispatch_feedback(tmp_path, record):
    relay = FakeRelay(record)
    relay.change_refs = True
    rejected_refs = set()

    def reject(prompt):
        ui = json.loads(prompt.split("\nUI_TEXT=", 1)[1])
        rejected_refs.update(item["ref"] for item in ui["elements"])
        ref = next(item["ref"] for item in ui["elements"] if item["role"] == "Button")
        return bad_input(ref=ref)

    def correct(prompt):
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        ui = json.loads(prompt.split("\nUI_TEXT=", 1)[1])
        assert not rejected_refs.intersection(item["ref"] for item in ui["elements"])
        assert relay.calls == 2 and not relay.actions
        feedback = state["feedback"][-1]
        assert feedback["reason"] == "target_not_text_input"
        assert "rejected before relay dispatch" in feedback["required_response"]
        ref = next(item["ref"] for item in ui["elements"] if item["role"] == "Edit")
        return bad_input(ref=ref)

    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [reject, correct, decision("stop", intent="stop")],
        relay=relay,
    )
    assert result.reason == "model_requested_stop"
    assert brain.query_value_verified
    assert len(relay.actions) == 1
    assert relay.actions[0].root.target.ref == "e201"
    observations = [
        event.payload["obs_id"] for event in brain.log.read_all() if event.kind == "observe"
    ]
    assert observations[0] != observations[1]
    assert str(relay.actions[0].root.obs_id) == observations[1]
    assert len(model.prompts) == 4


@pytest.mark.parametrize("limit", [0, 1, 3])
def test_retry_limit_is_configurable_and_never_dispatches_rejected_target(tmp_path, record, limit):
    result, brain, relay, model = run_brain(
        tmp_path,
        record,
        [bad_input()] * (limit + 1),
        settings=BrainSettings(max_input_target_retries=limit, max_subgoal_failures=5),
    )
    assert result.status == "stopped" and result.reason == "target_not_text_input"
    assert brain.input_target_retries == limit
    assert len(model.prompts) == limit + 2  # Plan plus rejected choices.
    assert not relay.actions
    assert repairs(brain)[-1]["retry_available"] is False


def test_existing_phase_failure_limit_can_stop_before_target_retry_allowance(tmp_path, record):
    result, brain, relay, _ = run_brain(
        tmp_path,
        record,
        [bad_input()] * 2,
        settings=BrainSettings(max_input_target_retries=3),
    )
    assert result.reason == "repeated_subgoal_failure:duplicate_query:hbl_no"
    assert brain.input_target_retries == 2
    assert not relay.actions


def test_retry_allowance_does_not_reset_after_progress_or_phase_change(tmp_path, record):
    result, brain, relay, _ = run_brain(
        tmp_path,
        record,
        [bad_input(), *success_steps(record)[:4], bad_input(kind="editor", ref="e7")],
    )
    assert result.reason == "target_not_text_input"
    assert brain.input_target_retries == 1
    assert brain.phase == "enter"
    assert len(relay.actions) == 3
    assert all(value == "" for value in relay.inputs.values())


def test_foreground_change_during_reobservation_stops_before_retry_call(tmp_path, record):
    class ForegroundChanges(FakeRelay):
        def observe(self):
            self.blocked = self.calls > 0
            return super().observe()

    result, brain, relay, model = run_brain(
        tmp_path, record, [bad_input()], relay=ForegroundChanges(record)
    )
    assert result.reason == "blocked_foreground"
    assert brain.input_target_retries == 1
    assert len(model.prompts) == 2
    assert not relay.actions


def test_unknown_cost_on_retry_stops_before_any_input(tmp_path, record, monkeypatch):
    original = ScriptedModel._generate

    def fail_retry(self, prompt, schema, *, images=None):
        if len(self.prompts) == 2:
            raise OSError("synthetic provider failure on correction")
        return original(self, prompt, schema, images=images)

    monkeypatch.setattr(ScriptedModel, "_generate", fail_retry)
    result, brain, relay, _ = run_brain(tmp_path, record, [bad_input()])
    assert result.reason == "unknown_model_call_cost"
    assert not relay.actions
    calls = [event for event in brain.log.read_all() if event.kind == "model_call"]
    assert len(calls) == 3 and calls[-1].cost.usd is None


def test_retry_honors_cost_cap_before_next_measured_selection(tmp_path, record):
    result, brain, relay, model = run_brain(
        tmp_path,
        record,
        [bad_input()],
        price=Decimal("0.04"),
        settings=BrainSettings(max_cost_usd=0.1),
    )
    assert result.reason == "case_cost_cap"
    assert brain.input_target_retries == 1
    assert len(model.prompts) == 2 and not relay.actions


@pytest.mark.parametrize("scenario", ["query-missing", "query-disabled", "editor-disabled"])
def test_missing_or_disabled_target_remains_a_hard_stop(tmp_path, record, scenario):
    class DisabledInput(FakeRelay):
        def observe(self):
            observation = super().observe()
            ref = self.field_refs["consignee"] if scenario == "editor-disabled" else "e1"
            for item in observation.elements:
                if item.ref == ref:
                    item.enabled = False
            return observation

    steps = success_steps(record)
    if scenario == "editor-disabled":
        index = next(
            i
            for i, step in enumerate(steps)
            if (step.get("action") or {}).get("source") == "consignee"
        )
        responses, delivered = steps[: index + 1], index - 1
    else:
        responses = [bad_input(ref="e999" if scenario == "query-missing" else "e1")]
        delivered = 0
    result, brain, relay, model = run_brain(
        tmp_path, record, responses, relay=DisabledInput(record)
    )
    assert result.reason == "target_not_current_or_enabled"
    assert brain.input_target_retries == 0
    assert len(model.prompts) == len(responses) + 1
    assert len(relay.actions) == delivered
    assert not repairs(brain)
    assert not [
        e for e in brain.log.read_all() if e.kind == "verify" and e.payload.get("ok") is False
    ]
    if scenario == "editor-disabled":
        assert relay.inputs["consignee"] == ""
        assert not any(
            a.root.type in {"set_text", "type_text"}
            and a.root.target.ref == relay.field_refs["consignee"]
            for a in relay.actions
        )


def test_dispatch_error_with_same_reason_never_uses_selection_retry(tmp_path, record):
    class AmbiguousDispatch(FakeRelay):
        def act(self, action):
            self.actions.append(action)
            raise GuardStop("target_not_text_input")

    result, brain, relay, model = run_brain(
        tmp_path, record, [bad_input(ref="e1")], relay=AmbiguousDispatch(record)
    )
    assert result.reason == "target_not_text_input"
    assert brain.input_target_retries == 0
    assert len(relay.actions) == 1 and len(model.prompts) == 2
    assert not repairs(brain)


def test_reselection_cannot_bypass_workflow_authorization(tmp_path, record):
    result, brain, relay, _ = run_brain(
        tmp_path,
        record,
        [
            bad_input(),
            decision(kind="editor", intent="save", action={"type": "click", "ref": "e2"}),
        ],
    )
    assert result.reason == "repeated_phase_failure:duplicate_query"
    assert brain.input_target_retries == 1
    assert not relay.actions and not brain.save_issued


def test_reselection_honors_existing_step_limit(tmp_path, record):
    result, brain, relay, model = run_brain(
        tmp_path,
        record,
        [bad_input()],
        settings=BrainSettings(max_steps=1),
    )
    assert result.reason == "max_steps"
    assert brain.input_target_retries == 1
    assert len(model.prompts) == 2 and not relay.actions


@pytest.mark.parametrize("limit", [-1, 4])
def test_retry_limit_cannot_be_unbounded(limit):
    with pytest.raises(ValidationError):
        BrainSettings(max_input_target_retries=limit)
