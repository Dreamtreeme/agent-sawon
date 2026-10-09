"""A rejected text target can be reselected before dispatch, under fixed bounds."""

import json
from decimal import Decimal

import pytest
from case_labels import labeled_case
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

from brain.graph.action_preparation import ActionGuardRequest, validate_action_guard
from brain.graph.engine import Brain, GuardStop
from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.graph.schemas import Decision
from brain.settings import BrainSettings
from sawon_runtime.storage import RunLog


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


def test_rejected_query_and_editor_targets_use_the_same_bounded_rediscovery(tmp_path, record):
    for phase, kind, ref in (
        ("duplicate_query", "query", "e2"),
        ("enter", "editor", "e7"),
    ):
        with labeled_case(phase):
            case_dir = tmp_path / phase
            relay = FakeRelay(record)
            relay.mode = kind
            log = RunLog(case_dir, case_id=record.case_id, agent_id="target-recovery-test")
            brain = Brain(
                record=record,
                goal="Enter the synthetic record",
                relay=relay,
                model=ScriptedModel(log, []),
                settings=BrainSettings(max_input_target_retries=2),
                case_dir=case_dir,
                run_log=log,
            )
            brain.phase = phase
            brain.duplicate_clear = phase == "enter"
            brain.observation = relay.observe()
            brain.decision = Decision.model_validate(bad_input(kind=kind, ref=ref))

            assert brain._retry_input_target_selection("target_not_text_input") == "observation"
            assert brain.input_target_retries == 1
            assert brain.phase_failures[phase] == 1
            assert relay.actions == []
            assert repairs(brain) == [
                {
                    "check": "input_target_reselection",
                    "ok": False,
                    "reason": "target_not_text_input",
                    "obs_id": str(brain.observation.obs_id),
                    "ref": ref,
                    "source": "hbl_no",
                    "action_dispatched": False,
                    "retries_used": 0,
                    "max_retries": 2,
                    "retry_available": True,
                }
            ]


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


def test_missing_target_is_a_hard_stop_without_reselection(tmp_path, record):
    result, brain, relay, model = run_brain(tmp_path, record, [bad_input(ref="e999")])
    assert result.reason == "target_not_current_or_enabled"
    assert brain.input_target_retries == 0
    assert len(model.prompts) == 2
    assert relay.actions == []
    assert not repairs(brain)
    assert not [
        e for e in brain.log.read_all() if e.kind == "verify" and e.payload.get("ok") is False
    ]


def test_action_guard_rejects_disabled_query_and_editor_inputs(record):
    relay = FakeRelay(record)
    cases = []
    query = relay.observe()
    query.elements = [
        item.model_copy(update={"enabled": False}) if item.ref == "e1" else item
        for item in query.elements
    ]
    cases.append(
        (
            query,
            Decision.model_validate(bad_input(ref="e1")),
            DecisionPolicy(ExecutionFacts("duplicate_query")),
            "duplicate_query",
        )
    )
    relay.mode = "editor"
    editor = relay.observe()
    ref = relay.field_refs["consignee"]
    editor.elements = [
        item.model_copy(update={"enabled": False}) if item.ref == ref else item
        for item in editor.elements
    ]
    cases.append(
        (
            editor,
            Decision.model_validate(bad_input(kind="editor", source="consignee", ref=ref)),
            DecisionPolicy(
                ExecutionFacts(
                    "enter",
                    field_names=("consignee",),
                    duplicate_clear=True,
                )
            ),
            "enter",
        )
    )
    for observation, proposed, policy, phase in cases:
        with pytest.raises(GuardStop, match="^target_not_current_or_enabled$"):
            validate_action_guard(
                ActionGuardRequest(
                    observation=observation,
                    decision=proposed,
                    policy=policy,
                    phase=phase,
                )
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


def test_retry_limit_has_both_lower_and_upper_bounds():
    for limit in (-1, 4):
        with labeled_case(limit):
            with pytest.raises(ValidationError) as raised:
                BrainSettings(max_input_target_retries=limit)
            assert raised.value.errors()
