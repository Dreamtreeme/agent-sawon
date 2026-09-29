"""Compiled-graph replay checks for the shared policy and action-effect contract."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from test_brain import (
    FakeRelay,
    ScriptedModel,
    decision,
    element,
    success_steps,
    text_choice,
)
from test_brain import record as record
from test_credential_progress import PASSWORD, USERNAME, LoginRelay, credential

from brain.graph.engine import Brain
from brain.graph.schemas import Plan
from brain.runtime.storage import RunLog, StateStore
from brain.settings import BrainSettings


class PolicyModel(ScriptedModel):
    def __init__(self, log, decisions):
        super().__init__(log, decisions)
        self.selection_schemas = []

    def _generate(self, prompt, schema, *, images=None):
        if schema is not Plan:
            self.selection_schemas.append(schema)
        return super()._generate(prompt, schema, images=images)


def task_state(prompt):
    return json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])


def schema_accepts(schema, response):
    wrapped = {"decision": response} if "decision" in schema.model_fields else response
    schema.model_validate(wrapped)


def schema_rejects(schema, response):
    with pytest.raises(ValidationError):
        schema_accepts(schema, response)


def make_run(tmp_path, record, relay, decisions, *, credentials=False):
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="policy-workflow-test")
    model = PolicyModel(log, decisions)
    brain = Brain(
        record=record,
        goal="Enter the synthetic record and save for approval",
        relay=relay,
        model=model,
        settings=BrainSettings(max_steps=30),
        case_dir=tmp_path,
        run_log=log,
        username=USERNAME if credentials else None,
        password=PASSWORD if credentials else None,
    )
    return brain, model


def transitions(brain):
    return [
        event.payload["transition"]
        for event in brain.log.read_all()
        if event.kind == "verify" and event.payload.get("check") == "action_transition"
    ]


def test_submitted_secret_is_removed_from_model_contract_and_never_dispatched_twice(
    tmp_path, record
):
    relay = LoginRelay(record)

    def repeat_submitted_password(prompt):
        state = task_state(prompt)
        assert state["credential_inputs_submitted"] == ["credential.password"]
        policy = state["execution_policy"]
        assert policy["remaining_credential_sources"] == ["credential.username"]
        assert policy["verified_facts"]["credential_submission_is_authentication"] is False
        credential_rule = next(
            rule for rule in policy["allowed_next_choices"] if rule["intent"] == "credential"
        )
        assert credential_rule["action"][0]["source"] == ["credential.username"]
        schema = model.selection_schemas[-1]
        schema_rejects(schema, credential("credential.password"))
        schema_accepts(schema, credential("credential.username"))
        return credential("credential.password")

    brain, model = make_run(
        tmp_path,
        record,
        relay,
        [credential("credential.password"), repeat_submitted_password, repeat_submitted_password],
        credentials=True,
    )
    result = brain.run()
    assert result.status == "stopped"
    assert result.reason.startswith("repeated_subgoal_failure:duplicate_query:")
    assert result.steps == 3
    assert relay.password_deliveries == 1
    assert relay.username_deliveries == relay.login_count == 0
    assert not relay.authenticated
    effects = transitions(brain)
    assert len(effects) == 1
    assert effects[0]["delivery"] == "delivered"
    assert effects[0]["observed_effect"] == "protected_input_reread"
    assert not any("authentication" in fact for fact in effects[0]["new_facts"])
    assert all(PASSWORD not in prompt for prompt in model.prompts)
    assert PASSWORD not in json.dumps(
        [event.model_dump(mode="json") for event in brain.log.read_all()]
    )


def test_unchanged_login_is_dispatched_once_then_stops_at_existing_failure_limit(tmp_path, record):
    relay = LoginRelay(record, accept_login=False)
    login = decision(kind="login", intent="login", action={"type": "click", "ref": "e15"})
    brain, model = make_run(
        tmp_path,
        record,
        relay,
        [credential("credential.username"), credential("credential.password"), login, login],
        credentials=True,
    )
    result = brain.run()
    assert result.status == "stopped"
    assert result.reason.startswith("repeated_")
    assert result.steps == 4
    assert len(model.prompts) == 5
    assert relay.username_deliveries == relay.password_deliveries == relay.login_count == 1
    assert not relay.authenticated
    assert not brain.verified
    assert not brain.save_issued
    failed = [
        event.payload
        for event in brain.log.read_all()
        if event.kind == "verify" and event.payload.get("ok") is False
    ]
    assert len(failed) == brain.settings.max_subgoal_failures == 2
    assert failed[-1]["phase_failures_without_progress"] == 2
    login_effects = [item for item in transitions(brain) if item["intent"] == "login"]
    assert len(login_effects) == 1
    assert login_effects[0]["delivery"] == "delivered"
    assert login_effects[0]["expected_effect"] == "login_result_observation"
    assert login_effects[0]["observed_effect"] == "no_change"
    assert login_effects[0]["new_facts"] == []
    assert not any(
        event.payload.get("check") == "final_query_fields" for event in brain.log.read_all()
    )


class PrefilledRelay(FakeRelay):
    def act(self, action):
        result = super().act(action)
        if action.root.type == "click" and action.root.target.ref == "e3":
            self.inputs.update(self.values)
        return result


def test_safe_wait_collects_current_editor_evidence_before_save_becomes_available(tmp_path, record):
    relay = PrefilledRelay(record)
    save = decision(intent="save", kind="editor", action={"type": "click", "ref": "e7"})

    def collect_evidence(prompt):
        policy = task_state(prompt)["execution_policy"]
        assert policy["current_step"] == "fill_fields"
        assert set(policy["remaining_fields"]) == set(relay.values)
        assert "save" not in {rule["intent"] for rule in policy["allowed_next_choices"]}
        schema_rejects(model.selection_schemas[-1], save)
        evidence_wait = decision(
            kind="editor",
            intent="wait",
            action={"type": "wait", "duration_ms": 0},
            evidence=[{"field": field, "ref": ref} for field, ref in relay.field_refs.items()],
        )
        schema_accepts(model.selection_schemas[-1], evidence_wait)
        return evidence_wait

    def save_after_evidence(prompt):
        assert set(brain.verified) == set(relay.values)
        policy = task_state(prompt)["execution_policy"]
        assert policy["current_step"] == "save"
        assert policy["remaining_fields"] == []
        assert "save" in {rule["intent"] for rule in policy["allowed_next_choices"]}
        schema_accepts(model.selection_schemas[-1], save)
        schema_rejects(
            model.selection_schemas[-1],
            decision(intent="fill", kind="editor", action=text_choice("hbl_no", "e20")),
        )
        return save

    brain, model = make_run(
        tmp_path,
        record,
        relay,
        [
            *success_steps(record)[:4],
            collect_evidence,
            save_after_evidence,
            *success_steps(record)[-3:],
        ],
    )
    result = brain.run()
    assert result.status == "success", result
    assert StateStore(tmp_path, case_id=record.case_id).read().status == "awaiting_approval"
    assert [action.root.type for action in relay.actions] == [
        "set_text",
        "click",
        "click",
        "wait",
        "click",
        "set_text",
        "click",
    ]
    assert not any(
        action.root.type in {"set_text", "type_text"}
        and action.root.target.ref in relay.field_refs.values()
        for action in relay.actions
    )


class ChangingVerifiedValueRelay(FakeRelay):
    def __init__(self, record):
        super().__init__(record)
        self.field_rereads = 0
        self.changed = False

    def observe(self):
        if self.inputs["hbl_no"] == self.values["hbl_no"] and not self.changed:
            self.field_rereads += 1
            if self.field_rereads == 2:
                self.inputs["hbl_no"] = "Synthetic externally changed value"
                self.changed = True
        return super().observe()


def test_visible_changed_field_reopens_fill_in_prompt_schema_before_selection(tmp_path, record):
    relay = ChangingVerifiedValueRelay(record)
    fill = decision(intent="fill", kind="editor", action=text_choice("hbl_no", "e20"))

    def refill_changed_field(prompt):
        assert relay.changed
        assert "hbl_no" not in brain.verified
        policy = task_state(prompt)["execution_policy"]
        assert "hbl_no" in policy["remaining_fields"]
        assert "hbl_no" not in policy["verified_facts"]["verified_fields"]
        schema_accepts(model.selection_schemas[-1], fill)
        schema_rejects(
            model.selection_schemas[-1],
            decision(intent="save", kind="editor", action={"type": "click", "ref": "e7"}),
        )
        return fill

    brain, model = make_run(
        tmp_path,
        record,
        relay,
        [*success_steps(record)[:4], fill, refill_changed_field, decision("stop", intent="stop")],
    )
    result = brain.run()
    assert result.reason == "model_requested_stop"
    assert (
        len(
            [
                action
                for action in relay.actions
                if action.root.type == "set_text" and action.root.target.ref == "e20"
            ]
        )
        == 2
    )
    assert "hbl_no" in brain.verified
    assert not brain.save_issued


class AlternatingRelay(FakeRelay):
    """A changing panel marker makes each navigation a real semantic UI change."""

    def __init__(self, record):
        super().__init__(record)
        self.side = False
        self.navigation_count = 0

    def observe(self):
        observation = super().observe()
        observation.elements.extend(
            [
                element("e90", "Button", name="Switch panel", parent="e0"),
                element("e91", "Text", name="Panel B" if self.side else "Panel A", parent="e0"),
            ]
        )
        return observation

    def act(self, action):
        if action.root.type == "click" and action.root.target.ref == "e90":
            self.side = not self.side
            self.navigation_count += 1
        return super().act(action)


def panel_navigation(kind="editor"):
    return decision(kind=kind, intent="navigate", action={"type": "click", "ref": "e90"})


def test_semantic_abab_cycle_stops_at_existing_failure_cap_without_business_progress(
    tmp_path, record
):
    relay = AlternatingRelay(record)
    brain, _ = make_run(
        tmp_path,
        record,
        relay,
        [*success_steps(record)[:4], *[panel_navigation() for _ in range(8)]],
    )
    result = brain.run()
    assert result.status == "stopped"
    assert result.reason.startswith("repeated_")
    assert relay.navigation_count <= 5
    cycle_failures = [
        event.payload
        for event in brain.log.read_all()
        if event.kind == "verify" and event.payload.get("reason") == "navigation_cycle"
    ]
    assert len(cycle_failures) == 2
    assert not brain.verified
    assert not brain.save_issued


@pytest.mark.parametrize("progress", ["query_input", "fill"])
def test_new_business_evidence_allows_a_normal_panel_revisit(tmp_path, record, progress):
    relay = AlternatingRelay(record)
    kind = "query" if progress == "query_input" else "editor"
    prefix = [] if progress == "query_input" else success_steps(record)[:4]
    field = decision(
        kind=kind,
        intent=progress,
        action=text_choice("hbl_no", "e1" if progress == "query_input" else "e20"),
    )
    brain, _ = make_run(
        tmp_path,
        record,
        relay,
        [
            *prefix,
            panel_navigation(kind),
            panel_navigation(kind),
            field,
            panel_navigation(kind),
            panel_navigation(kind),
            decision("stop", kind=kind, intent="stop"),
        ],
    )
    result = brain.run()
    assert result.reason == "model_requested_stop", result
    assert relay.navigation_count == 4
    assert not any(
        event.payload.get("reason") == "navigation_cycle" for event in brain.log.read_all()
    )
    assert brain.query_value_verified if progress == "query_input" else "hbl_no" in brain.verified


def test_action_delivery_and_final_proof_are_separate_auditable_events(tmp_path, record):
    relay = FakeRelay(record)
    brain, _ = make_run(tmp_path, record, relay, success_steps(record))
    result = brain.run()
    assert result.status == "success", result
    effects = transitions(brain)
    assert len(effects) == len(relay.actions)
    assert len({item["action_id"] for item in effects}) == len(effects)
    assert all(item["before_obs_id"] and item["after_obs_id"] for item in effects)
    assert all(item["delivery"] == "delivered" for item in effects)
    save_effect = next(item for item in effects if item["intent"] == "save")
    assert save_effect["observed_effect"] != "final_query_fields"
    final_proofs = [
        event.payload
        for event in brain.log.read_all()
        if event.kind == "verify" and event.payload.get("check") == "final_query_fields"
    ]
    assert len(final_proofs) == 1
    assert final_proofs[0]["ok"] is True
    assert set(final_proofs[0]["fields"]) == set(relay.values)
    assert StateStore(tmp_path, case_id=record.case_id).read().status == "awaiting_approval"
