"""Replay regressions for mandatory, bounded post-search duplication evidence."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_brain import (
    FakeRelay,
    ScriptedModel,
    decision,
    element,
    query_steps,
    run_brain,
    success_steps,
    text_choice,
)
from test_brain import (
    record as record,
)

from brain.graph.engine import Brain
from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.graph.schemas import Choice, Decision
from brain.observe.progress import navigation_state
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings


def clear_decision(grid_ref="e4"):
    return decision("duplicate_clear", intent="verify", grid_ref=grid_ref)


def failures(brain):
    return [
        event.payload
        for event in brain.log.read_all()
        if event.kind == "verify" and event.payload.get("ok") is False
    ]


def consignee_step(steps):
    return next(
        index
        for index, step in enumerate(steps)
        if (step.get("action") or {}).get("source") == "consignee"
    )


class PostSaveEditorRelay(FakeRelay):
    """Saving leaves the editor visible until an observed query control is used."""

    def __init__(self, record, *, disable_saved_field=False):
        super().__init__(record)
        self.save_count = 0
        self.post_save_field_writes = 0
        self.disable_saved_field = disable_saved_field

    def observe(self):
        observation = super().observe()
        if self.saved and self.mode == "editor":
            observation.elements.append(element("e9", "Button", name="Find objects", parent="e0"))
            if self.disable_saved_field:
                next(
                    item for item in observation.elements if item.ref == self.field_refs["hbl_no"]
                ).enabled = False
        return observation

    def act(self, action):
        item = action.root
        if (
            self.saved
            and item.type in {"set_text", "type_text"}
            and item.target.ref in self.field_refs.values()
        ):
            self.post_save_field_writes += 1
        result = super().act(action)
        if item.type == "click" and item.target.ref == "e7":
            self.save_count += 1
            self.mode = "editor"
        elif item.type == "click" and item.target.ref == "e9":
            self.mode = "query"
        return result


def test_login_typing_intent_is_repaired_before_credential_reaches_relay(tmp_path, record):
    relay = FakeRelay(record)
    relay.mode = "login"
    invalid = decision(
        kind="login", intent="login", action=text_choice("credential.password", "e8")
    )

    def corrected_credential(prompt):
        assert relay.mode == "login"
        assert not relay.actions
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert any(
            item.get("reason") == "selection_error:ValidationError" for item in state["feedback"]
        )
        policy = state["execution_policy"]
        assert policy["remaining_credential_sources"] == ["credential.password"]
        credential_rule = next(
            rule for rule in policy["allowed_next_choices"] if rule["intent"] == "credential"
        )
        assert credential_rule["action"][0]["source"] == ["credential.password"]
        return decision(
            kind="login", intent="credential", action=text_choice("credential.password", "e8")
        )

    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [invalid, corrected_credential, decision("stop", intent="stop", kind="login")],
        relay=relay,
        password="synthetic-login-fixture",
    )
    assert result.reason == "model_requested_stop"
    assert result.steps == 3
    assert len(model.prompts) == 4
    assert len(relay.actions) == 1
    assert relay.actions[0].root.text == "synthetic-login-fixture"
    assert [item["reason"] for item in failures(brain)] == ["selection_error:ValidationError"]
    assert "synthetic-login-fixture" not in brain.log.path.read_text()
    assert all("synthetic-login-fixture" not in prompt for prompt in model.prompts)


@pytest.mark.parametrize("action_type", ["set_text", "type_text"])
def test_credential_typing_outside_login_is_bounded_without_any_write(
    tmp_path, record, action_type
):
    invalid = decision(
        kind="query",
        intent="credential",
        action={"type": action_type, "ref": "e1", "source": "credential.password"},
    )
    policy = DecisionPolicy(
        ExecutionFacts("duplicate_query", available_credential_sources=("credential.password",))
    )
    # Changing only the claimed screen must distinguish the allowed and rejected branch.
    assert policy.allows(Decision.model_validate({**invalid, "screen_kind": "login"}))
    assert not policy.allows(Decision.model_validate(invalid))
    result, brain, relay, model = run_brain(
        tmp_path,
        record,
        [invalid, invalid],
        password="synthetic-login-fixture",
    )
    assert result.status == "stopped"
    assert result.reason == "repeated_subgoal_failure:duplicate_query:decision"
    assert result.steps == 2
    assert len(model.prompts) == 3
    assert not relay.actions
    assert [item["reason"] for item in failures(brain)] == [
        "selection_error:ValidationError",
        "selection_error:ValidationError",
    ]
    assert not brain.query_submitted and not brain.save_issued
    assert "synthetic-login-fixture" not in brain.log.path.read_text()
    assert all("synthetic-login-fixture" not in prompt for prompt in model.prompts)
    assert all(
        "synthetic-login-fixture" not in path.read_text()
        for path in (tmp_path / "observations").glob("*.json")
    )


def test_missing_field_blocks_save_until_explicit_fill_and_workflow_completes(tmp_path, record):
    steps = success_steps(record)
    missing_field = "pol"
    missing_step = next(
        step for step in steps if (step.get("action") or {}).get("source") == missing_field
    )
    save_index = next(index for index, step in enumerate(steps) if step["intent"] == "save")
    before_save = [step for step in steps[:save_index] if step is not missing_step]
    relay = FakeRelay(record)

    def fill_missing_field(prompt):
        assert relay.mode == "editor"
        assert not relay.saved
        assert relay.inputs[missing_field] == ""
        assert len(relay.actions) == len(before_save) - 1
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert state["remaining_fields"] == [missing_field]
        assert state["save_issued"] is False
        assert any(
            item.get("reason") == "selection_error:ValidationError" for item in state["feedback"]
        )
        policy = state["execution_policy"]
        assert policy["remaining_fields"] == [missing_field]
        assert "save" not in {rule["intent"] for rule in policy["allowed_next_choices"]}
        fill_rule = next(
            rule for rule in policy["allowed_next_choices"] if rule["intent"] == "fill"
        )
        assert fill_rule["action"][0]["source"] == [missing_field]
        return missing_step

    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [*before_save, steps[save_index], fill_missing_field, *steps[save_index:]],
        relay=relay,
    )
    assert result.status == "success", result
    assert result.steps == len(steps) + 1
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(steps) - 2
    assert relay.inputs[missing_field] == relay.values[missing_field]
    assert [item["reason"] for item in failures(brain)] == ["selection_error:ValidationError"]
    assert relay.saved


def test_repeated_save_with_missing_field_stops_without_filling_or_saving(tmp_path, record):
    steps = success_steps(record)
    missing_field = "pol"
    save_index = next(index for index, step in enumerate(steps) if step["intent"] == "save")
    before_save = [
        step
        for step in steps[:save_index]
        if (step.get("action") or {}).get("source") != missing_field
    ]
    result, brain, relay, model = run_brain(
        tmp_path, record, [*before_save, steps[save_index], steps[save_index]]
    )
    assert result.status == "stopped"
    assert result.reason.startswith("repeated_")
    assert result.steps == len(before_save) + 2
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(before_save) - 1
    assert relay.inputs[missing_field] == ""
    assert all(
        value == relay.values[field]
        for field, value in relay.inputs.items()
        if field != missing_field
    )
    assert [item["reason"] for item in failures(brain)] == [
        "selection_error:ValidationError",
        "selection_error:ValidationError",
    ]
    assert not relay.saved
    assert not brain.save_issued


def test_post_save_fill_repairs_to_explicit_query_navigation_and_completes(tmp_path, record):
    steps = success_steps(record)
    save_index = next(index for index, step in enumerate(steps) if step["intent"] == "save")
    relay = PostSaveEditorRelay(record)
    invalid = decision(
        kind="editor", intent="fill", action=text_choice("hbl_no", relay.field_refs["hbl_no"])
    )

    def navigate_to_query(prompt):
        assert relay.saved and relay.mode == "editor"
        assert relay.save_count == 1
        assert relay.post_save_field_writes == 0
        assert len(relay.actions) == save_index
        assert relay.inputs == relay.values
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert state["phase"] == "save_result"
        assert state["save_issued"] is True
        assert state["duplicate_clear"] is True
        assert state["query_value_verified"] is False
        assert any(item.get("reason") == "save_result_required" for item in state["feedback"])
        summary = json.loads(prompt.split("\nUI_TEXT=", 1)[1])
        assert any(item["ref"] == "e9" for item in summary["elements"])
        return decision(kind="editor", intent="navigate", action={"type": "click", "ref": "e9"})

    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [*steps[: save_index + 1], invalid, navigate_to_query, *steps[save_index + 1 :]],
        relay=relay,
    )
    assert result.status == "success", result
    assert result.steps == len(steps) + 2
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(steps) - 1
    assert relay.save_count == 1
    assert relay.post_save_field_writes == 0
    assert relay.inputs == relay.values
    assert [item["reason"] for item in failures(brain)] == ["save_result_required"]


def test_repeated_post_save_fill_stops_without_another_write_or_save(tmp_path, record):
    steps = success_steps(record)
    save_index = next(index for index, step in enumerate(steps) if step["intent"] == "save")
    relay = PostSaveEditorRelay(record)
    invalid = decision(
        kind="editor", intent="fill", action=text_choice("hbl_no", relay.field_refs["hbl_no"])
    )
    result, brain, _, model = run_brain(
        tmp_path, record, [*steps[: save_index + 1], invalid, invalid], relay=relay
    )
    assert result.status == "stopped"
    assert result.reason.startswith("repeated_")
    assert result.steps == save_index + 3
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == save_index
    assert relay.mode == "editor"
    assert relay.save_count == 1
    assert relay.post_save_field_writes == 0
    assert relay.inputs == relay.values
    assert brain.phase == "save_result" and brain.save_issued and brain.duplicate_clear
    assert [item["reason"] for item in failures(brain)] == [
        "save_result_required",
        "save_result_required",
    ]


@pytest.mark.parametrize("target_state", ["missing", "disabled"])
def test_post_save_navigation_invalid_target_remains_fatal(tmp_path, record, target_state):
    steps = success_steps(record)
    save_index = next(index for index, step in enumerate(steps) if step["intent"] == "save")
    relay = PostSaveEditorRelay(record, disable_saved_field=target_state == "disabled")
    ref = "e999" if target_state == "missing" else relay.field_refs["hbl_no"]
    # Keep the target-safety test on an eligible intent so policy validation does
    # not reject the response before the current/disabled target guard runs.
    invalid = decision(kind="editor", intent="navigate", action={"type": "click", "ref": ref})
    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [
            *steps[: save_index + 1],
            {
                "result": "ready_for_query",
                "evidence_refs": ["e9"],
                "repair_fields": [],
                "next_decision": {"decision": invalid},
            },
        ],
        relay=relay,
    )
    assert result.status == "stopped"
    assert result.reason == "target_not_current_or_enabled"
    assert result.steps == save_index + 2
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == save_index
    assert relay.save_count == 1
    assert relay.post_save_field_writes == 0
    assert relay.inputs == relay.values
    assert not failures(brain)
    assert brain.phase == "final_query" and brain.save_issued and brain.duplicate_clear


def test_null_text_target_is_repaired_before_write_and_workflow_completes(tmp_path, record):
    steps = success_steps(record)
    index = consignee_step(steps)
    invalid = {**steps[index], "action": {**steps[index]["action"], "ref": None}}
    relay = FakeRelay(record)

    def corrected_target(prompt):
        assert relay.inputs["consignee"] == ""
        assert len(relay.actions) == index - 1
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert any(item.get("ok") is False for item in state["feedback"])
        return steps[index]

    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [*steps[:index], invalid, corrected_target, *steps[index + 1 :]],
        relay=relay,
    )
    assert result.status == "success", result
    assert result.steps == len(steps) + 1
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(steps) - 2
    assert len(failures(brain)) == 1


@pytest.mark.parametrize(
    "choice",
    [
        {"type": "set_text", "ref": " ", "source": "consignee"},
        {"type": "set_text", "ref": "e23"},
    ],
    ids=["blank-ref", "missing-text-source"],
)
def test_text_choice_requires_nonempty_target_and_source(choice):
    with pytest.raises(ValidationError):
        Choice.model_validate(choice)


def navigation_verifier(tmp_path, record, *, observed_handle):
    class WindowRelay(FakeRelay):
        def observe(self):
            observation = super().observe()
            window = observation.active_window.model_copy(update={"handle": observed_handle})
            return observation.model_copy(update={"active_window": window, "windows": [window]})

    log = RunLog(tmp_path, case_id=record.case_id, agent_id="checkpoint-test")
    brain = Brain(
        record=record,
        goal="Synthetic navigation verification",
        relay=WindowRelay(record),
        model=ScriptedModel(log, []),
        settings=BrainSettings(),
        case_dir=tmp_path,
        run_log=log,
    )
    brain.phase = "enter"
    brain.phase_failures["enter"] = 1
    brain.failures["enter:decision"] = 1
    return brain


def test_new_navigation_window_resets_failure_once_and_cycle_cannot_erase_it(tmp_path, record):
    brain = navigation_verifier(tmp_path, record, observed_handle=2)
    parsed = Decision.model_validate(decision(action={"type": "click", "ref": "e3"}))
    pending = {
        "choice": parsed.action,
        "decision": parsed,
        "source_window_handle": 1,
        "source_navigation_state": navigation_state(FakeRelay(record).observe()),
    }
    brain.pending = pending.copy()
    assert brain._verify_node({"next": "verify"}) == {"next": "observe"}
    assert brain.phase_failures["enter"] == 0
    assert brain.failures["enter:decision"] == 1
    assert brain.business_revision == 0

    # Repeating A->B implies the intervening return to A. The A-B-A-B history
    # now consumes the remaining failure allowance without new business proof.
    brain.phase_failures["enter"] = 1
    brain.pending = pending.copy()
    assert brain._verify_node({"next": "verify"}) == {"next": "finish"}
    assert brain.phase_failures["enter"] == 2
    assert brain.failures["enter:decision"] == 2
    assert failures(brain)[-1]["reason"] == "navigation_cycle"
    assert brain.business_revision == 0


@pytest.mark.parametrize(
    "intent,action,observed_handle",
    [
        ("navigate", {"type": "click", "ref": "e3"}, 1),
        ("wait", {"type": "wait", "duration_ms": 0}, 2),
    ],
    ids=["same-window-navigation", "wait-even-with-window-change"],
)
def test_no_navigation_progress_cannot_reset_phase_failure(
    tmp_path, record, intent, action, observed_handle
):
    brain = navigation_verifier(tmp_path, record, observed_handle=observed_handle)
    parsed = Decision.model_validate(decision(intent=intent, action=action))
    brain.pending = {
        "choice": parsed.action,
        "decision": parsed,
        "source_window_handle": 1,
        "source_navigation_state": navigation_state(FakeRelay(record).observe()),
    }
    outcome = brain._verify_node({"next": "verify"})
    if intent == "navigate":
        # An unchanged navigation now consumes the remaining failure allowance.
        assert outcome == {"next": "finish"}
        assert brain.phase_failures["enter"] == 2
        assert failures(brain)[-1]["reason"] == "navigation_no_progress"
    else:
        assert outcome == {"next": "observe"}
        assert brain.phase_failures["enter"] == 1
        assert brain.failures["enter:decision"] == 1


def test_entry_repairs_action_intent_mismatch_before_navigation(tmp_path, record):
    relay = FakeRelay(record)
    steps = success_steps(record)
    invalid = decision(intent="verify", action={"type": "click", "ref": "e3"})

    def corrected_navigation(prompt):
        assert relay.mode == "query"
        assert len(relay.actions) == 2
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert state["phase"] == "enter"
        assert state["duplicate_clear"] is True
        assert any(item.get("ok") is False for item in state["feedback"])
        return steps[3]

    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [*steps[:3], invalid, corrected_navigation, *steps[4:]],
        relay=relay,
    )
    assert result.status == "success", result
    assert result.steps == len(steps) + 1
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(steps) - 2
    assert len(failures(brain)) == 1


@pytest.mark.parametrize("scenario", ["login-intent", "entry-intent", "null-text-target"])
def test_repeated_malformed_decisions_are_bounded_before_dispatch(tmp_path, record, scenario):
    relay = FakeRelay(record)
    steps = success_steps(record)
    password = None
    if scenario == "login-intent":
        relay.mode = "login"
        password = "synthetic-login-fixture"
        prefix = []
        invalid = decision(
            kind="login", intent="login", action=text_choice("credential.password", "e8")
        )
    elif scenario == "entry-intent":
        prefix = steps[:3]
        invalid = decision(intent="verify", action={"type": "click", "ref": "e3"})
    else:
        index = consignee_step(steps)
        prefix = steps[:index]
        invalid = {**steps[index], "action": {**steps[index]["action"], "ref": None}}
    result, brain, relay, model = run_brain(
        tmp_path, record, [*prefix, invalid, invalid], relay=relay, password=password
    )
    phase = "duplicate_query" if scenario == "login-intent" else "enter"
    assert result.status == "stopped"
    assert result.reason == f"repeated_subgoal_failure:{phase}:decision"
    assert result.steps == len(prefix) + 2
    assert len(model.prompts) == result.steps + 1
    assert [item["reason"] for item in failures(brain)] == ["selection_error:ValidationError"] * 2
    assert len(relay.actions) == (len(prefix) - 1 if prefix else 0)
    assert brain.duplicate_clear is (scenario != "login-intent")
    assert not brain.query_submitted and not brain.save_issued and not relay.saved
    if scenario == "null-text-target":
        assert relay.mode == "editor" and relay.inputs["consignee"] == ""
        for step in prefix:
            source = (step.get("action") or {}).get("source")
            if step["intent"] == "fill":
                assert relay.inputs[source] == relay.values[source]
    else:
        assert relay.mode == ("login" if scenario == "login-intent" else "query")


def test_completion_with_attached_action_needs_repair_without_executing_action(tmp_path, record):
    steps = success_steps(record)
    invalid = {**steps[-1], "action": {"type": "click", "ref": "e2"}}
    relay = FakeRelay(record)

    def corrected_completion(prompt):
        assert relay.mode == "query"
        assert len(relay.actions) == len(steps) - 2
        return steps[-1]

    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [*steps[:-1], invalid, corrected_completion],
        relay=relay,
    )
    assert result.status == "success", result
    assert result.steps == len(steps) + 1
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(steps) - 2
    assert len(failures(brain)) == 1


def test_final_evidence_mislabeled_as_action_repairs_to_complete_without_ui_action(
    tmp_path, record
):
    steps = success_steps(record)
    invalid = {**steps[-1], "operation": "act", "intent": "verify", "action": None}
    relay = FakeRelay(record)

    def corrected_completion(prompt):
        assert relay.saved and relay.mode == "query"
        assert len(relay.actions) == len(steps) - 2
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert state["phase"] == "final_query"
        assert state["query_submitted"] is True
        assert state["query_value_verified"] is True
        assert any(
            item.get("reason") == "selection_error:ValidationError" for item in state["feedback"]
        )
        policy = state["execution_policy"]
        assert policy["current_step"] == "final_row_evidence"
        completion = next(
            rule for rule in policy["allowed_next_choices"] if rule["operation"] == "complete"
        )
        assert completion["intent"] == "verify" and completion["action"] is None
        assert "all_field_evidence" in completion["requires"]
        return steps[-1]

    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [*steps[:-1], invalid, corrected_completion],
        relay=relay,
    )
    assert result.status == "success", result
    assert result.steps == len(steps) + 1
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(steps) - 2
    assert [item["reason"] for item in failures(brain)] == ["selection_error:ValidationError"]
    final_checks = [
        event
        for event in brain.log.read_all()
        if event.kind == "verify" and event.payload.get("check") == "final_query_fields"
    ]
    assert len(final_checks) == 1
    assert final_checks[0].payload["ok"] is True


def test_final_query_requires_fresh_input_before_search_and_can_recover(tmp_path, record):
    steps = success_steps(record)
    relay = FakeRelay(record)
    ready_review = {
        "result": "ready_for_query",
        "evidence_refs": ["e2"],
        "repair_fields": [],
        "next_decision": {
            "decision": decision(intent="wait", action={"type": "wait", "duration_ms": 0})
        },
    }

    def premature_search(prompt):
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert state["phase"] == "final_query"
        assert not state["query_value_verified"] and not state["query_submitted"]
        assert relay.saved and relay.query == relay.values["hbl_no"]
        assert len(relay.actions) == len(steps) - 3  # Includes the allowed review wait.
        return steps[-2]

    def corrected_input(prompt):
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert state["phase"] == "final_query"
        assert not state["query_value_verified"] and not state["query_submitted"]
        assert state["execution_policy"]["current_step"] == "query_input"
        assert "search" not in {
            rule["intent"] for rule in state["execution_policy"]["allowed_next_choices"]
        }
        assert any(item.get("reason") == "final_query_required" for item in state["feedback"])
        assert len(relay.actions) == len(steps) - 3  # Rejected search did not dispatch.
        return steps[-3]

    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [*steps[:-3], ready_review, premature_search, corrected_input, *steps[-2:]],
        relay=relay,
    )
    assert result.status == "success", result
    assert result.steps == len(steps) + 2
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(steps) - 1
    assert [item["reason"] for item in failures(brain)] == ["final_query_required"]


def test_premature_final_completion_repairs_through_explicit_input_search_and_evidence(
    tmp_path, record
):
    steps = success_steps(record)
    relay = FakeRelay(record)

    def fresh_query_input(prompt):
        assert relay.saved
        assert len(relay.actions) == len(steps) - 4
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert state["phase"] == "save_result"
        assert state["query_submitted"] is False
        assert state["query_value_verified"] is False
        assert state["next_required_step"] is None
        assert state["execution_policy"]["current_step"] == "save_result"
        assert state["final_required_fields"] == []
        assert any(item.get("reason") == "save_result_required" for item in state["feedback"])
        return steps[-3]

    def explicit_search(prompt):
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert state["query_value_verified"] is True
        assert state["query_submitted"] is False
        assert state["next_required_step"] == "search"
        return steps[-2]

    def final_row_evidence(prompt):
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert state["query_value_verified"] is True
        assert state["query_submitted"] is True
        assert state["next_required_step"] == "final_row_evidence"
        return steps[-1]

    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [*steps[:-3], steps[-1], fresh_query_input, explicit_search, final_row_evidence],
        relay=relay,
    )
    assert result.status == "success", result
    assert result.steps == len(steps) + 1
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(steps) - 2
    assert [item["reason"] for item in failures(brain)] == ["save_result_required"]


@pytest.mark.parametrize("proposal", ["complete", "search"])
def test_repeated_commands_before_save_review_are_bounded_without_dispatch(
    tmp_path, record, proposal
):
    steps = success_steps(record)
    invalid = steps[-1] if proposal == "complete" else steps[-2]
    result, brain, relay, model = run_brain(tmp_path, record, [*steps[:-3], invalid, invalid])
    assert result.status == "stopped"
    assert result.reason == "repeated_subgoal_failure:save_result:decision"
    assert result.steps == len(steps) - 1
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(steps) - 4
    assert relay.saved and relay.saved_snapshot == relay.inputs == relay.values
    assert brain.phase == "save_result" and brain.save_issued and brain.duplicate_clear
    assert not brain.query_value_verified and not brain.query_submitted
    assert [item["reason"] for item in failures(brain)] == ["save_result_required"] * 2
    assert relay.actions[-1].root.target.ref == "e7"


def test_incomplete_final_evidence_requires_observed_navigation_and_fresh_visible_cells(
    tmp_path, record
):
    missing_fields = {"container_no", "pol", "pod"}

    class NarrowQueryRelay(FakeRelay):
        def __init__(self, record):
            super().__init__(record)
            self.expanded = False
            self.hidden_refs = {
                f"e{index + 40}"
                for index, field in enumerate(self.values)
                if field in missing_fields
            }

        def observe(self):
            observation = super().observe()
            if self.saved and self.mode == "query":
                observation.elements.append(element("e9", "Button", name="Maximize", parent="e0"))
                if not self.expanded:
                    observation.elements = [
                        item for item in observation.elements if item.ref not in self.hidden_refs
                    ]
            return observation

        def act(self, action):
            result = super().act(action)
            if action.root.type == "click" and action.root.target.ref == "e9":
                self.expanded = True
            return result

    steps = success_steps(record)
    incomplete = {
        **steps[-1],
        "evidence": [item for item in steps[-1]["evidence"] if item["field"] not in missing_fields],
    }
    relay = NarrowQueryRelay(record)

    def reveal_columns(prompt):
        assert not relay.expanded
        assert len(relay.actions) == len(steps) - 2
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert state["next_required_step"] == "final_row_evidence"
        repair = next(
            item
            for item in state["feedback"]
            if item.get("reason") == "selection_error:ValidationError"
        )
        assert repair["validation_errors"]
        assert state["evidence_review"]["missing_fields"] == sorted(missing_fields)
        assert state["evidence_review"]["status"] == "needs_more"
        summary = json.loads(prompt.split("\nUI_TEXT=", 1)[1])
        assert not relay.hidden_refs.intersection(item["ref"] for item in summary["elements"])
        assert any(item["ref"] == "e9" for item in summary["elements"])
        return decision(intent="navigate", action={"type": "click", "ref": "e9"})

    def prove_visible_row(prompt):
        assert relay.expanded
        assert len(relay.actions) == len(steps) - 1
        summary = json.loads(prompt.split("\nUI_TEXT=", 1)[1])
        assert relay.hidden_refs.issubset(item["ref"] for item in summary["elements"])
        return steps[-1]

    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [*steps[:-1], incomplete, reveal_columns, prove_visible_row],
        relay=relay,
    )
    assert result.status == "success", result
    assert result.steps == len(steps) + 2
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(steps) - 1
    assert [item["reason"] for item in failures(brain)] == ["selection_error:ValidationError"]
    final_checks = [
        event.payload
        for event in brain.log.read_all()
        if event.kind == "verify" and event.payload.get("check") == "final_query_fields"
    ]
    assert len(final_checks) == 1
    assert set(final_checks[0]["fields"]) == set(relay.values)
    assert final_checks[0]["row_ref"] == "e6"


def test_initial_query_search_before_input_can_recover_without_running_search(tmp_path, record):
    steps = success_steps(record)
    relay = FakeRelay(record)

    def corrected_input(prompt):
        assert not relay.actions
        assert relay.query == ""
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert state["phase"] == "duplicate_query"
        assert any(
            item.get("reason") == "selection_error:ValidationError" for item in state["feedback"]
        )
        policy = state["execution_policy"]
        assert policy["current_step"] == "query_input"
        query_rule = next(
            rule for rule in policy["allowed_next_choices"] if rule["intent"] == "query_input"
        )
        assert query_rule["action"][0]["source"] == ["hbl_no"]
        return steps[0]

    result, brain, _, model = run_brain(
        tmp_path, record, [steps[1], corrected_input, *steps[1:]], relay=relay
    )
    assert result.status == "success", result
    assert result.steps == len(steps) + 1
    assert len(model.prompts) == result.steps + 1
    assert len(relay.actions) == len(steps) - 2
    assert [item["reason"] for item in failures(brain)] == ["selection_error:ValidationError"]


@pytest.mark.parametrize(
    "invalid",
    [
        decision(action={"type": "click", "ref": "e3"}),
        decision(intent="wait", action={"type": "wait", "duration_ms": 0}),
        decision("complete", intent="verify", grid_ref="e4"),
        clear_decision(None),
        clear_decision(""),
        clear_decision("e1"),
        {**clear_decision(), "action": {"type": "click", "ref": "e3"}},
    ],
    ids=["navigate", "wait", "complete", "missing-grid", "empty-grid", "edit-as-grid", "action"],
)
def test_pending_checkpoint_repairs_evidence_before_any_navigation(tmp_path, record, invalid):
    relay = FakeRelay(record)

    def corrected_evidence(prompt):
        # The rejected decision must not have moved away from its result evidence.
        assert relay.mode == "query"
        assert len(relay.actions) == 2
        state = json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])
        assert state["query_submitted"] is True
        assert state["duplicate_clear"] is False
        assert any(item.get("ok") is False for item in state["feedback"])
        return clear_decision()

    steps = success_steps(record)
    result, brain, _, model = run_brain(
        tmp_path, record, [*steps[:2], invalid, corrected_evidence, *steps[3:]], relay=relay
    )
    assert result.status == "success", result
    assert result.steps == len(steps) + 1
    assert len(model.prompts) == result.steps + 1  # Includes the measured planning call.
    assert len(relay.actions) == len(steps) - 2
    assert len(failures(brain)) == 1
    assert relay.saved


def test_repeated_invalid_checkpoint_is_bounded_without_navigation(tmp_path, record):
    navigate = decision(action={"type": "click", "ref": "e3"})
    result, brain, relay, model = run_brain(tmp_path, record, [*query_steps(), navigate, navigate])
    assert result.status == "stopped"
    assert result.reason.startswith("repeated_")
    assert result.steps == 4
    assert len(model.prompts) == 5
    assert len(failures(brain)) == 2
    assert len(relay.actions) == 2
    assert relay.mode == "query"
    assert not relay.saved
    assert not brain.duplicate_clear


def test_invalid_checkpoint_counts_toward_step_cap(tmp_path, record):
    result, brain, relay, model = run_brain(
        tmp_path,
        record,
        [*query_steps(), clear_decision(None)],
        settings=BrainSettings(max_steps=3),
    )
    assert result.reason == "max_steps"
    assert result.steps == 3
    assert len(model.prompts) == 4
    assert len(failures(brain)) == 1
    assert len(relay.actions) == 2


def test_invalid_checkpoint_counts_toward_cost_cap(tmp_path, record):
    result, brain, relay, model = run_brain(
        tmp_path,
        record,
        [*query_steps(), clear_decision(None)],
        price=Decimal("0.02"),
        settings=BrainSettings(max_cost_usd=0.1, model_call_reserve_usd=0.025),
    )
    assert result.reason == "case_cost_cap"
    assert len(model.prompts) == 4
    assert len(failures(brain)) == 1
    assert len(relay.actions) == 2
    calls = [event for event in brain.log.read_all() if event.kind == "model_call"]
    assert sum(Decimal(str(event.cost.usd)) for event in calls) == Decimal("0.08")


@pytest.mark.parametrize("operation", ["duplicate_clear", "duplicate_found"])
def test_same_identifier_duplicate_stops_without_repair_or_entry(tmp_path, record, operation):
    result, brain, relay, model = run_brain(
        tmp_path,
        record,
        [*query_steps(), decision(operation, intent="verify", grid_ref="e4")],
        relay=FakeRelay(record, duplicate=True),
    )
    assert result.reason == "duplicate_record"
    assert len(model.prompts) == 4
    assert len(relay.actions) == 2
    assert not failures(brain)
    assert not brain.duplicate_clear


def test_nonempty_unmatched_result_cannot_be_cleared(tmp_path, record):
    class UnmatchedRelay(FakeRelay):
        def observe(self):
            observation = super().observe()
            for item in observation.elements:
                if item.ref == "e40":
                    item.value = "UNRELATED-IDENTIFIER"
            return observation

    result, brain, relay, _ = run_brain(
        tmp_path,
        record,
        [*query_steps(), clear_decision(), clear_decision()],
        relay=UnmatchedRelay(record, duplicate=True),
    )
    assert result.reason.startswith("repeated_")
    assert [item["reason"] for item in failures(brain)] == ["query_not_empty", "query_not_empty"]
    assert len(relay.actions) == 2
    assert not brain.duplicate_clear


@pytest.mark.parametrize(
    "change,expected",
    [
        ("foreground", "blocked_foreground"),
        ("window", "query_value_changed"),
        ("query", "query_value_changed"),
    ],
)
def test_environment_identity_changes_remain_fatal_at_checkpoint(
    tmp_path, record, change, expected
):
    class ChangedRelay(FakeRelay):
        def observe(self):
            observation = super().observe()
            if len(self.actions) >= 2:
                if change == "foreground":
                    observation.blocked_reason = "outside_allowlist"
                elif change == "window":
                    changed_window = observation.active_window.model_copy(update={"handle": 99})
                    observation = observation.model_copy(
                        update={"active_window": changed_window, "windows": [changed_window]}
                    )
                else:
                    next(
                        item for item in observation.elements if item.ref == "e1"
                    ).value = "CHANGED-IDENTIFIER"
            return observation

    result, brain, relay, _ = run_brain(
        tmp_path,
        record,
        [*query_steps(), clear_decision()],
        relay=ChangedRelay(record),
    )
    assert result.reason == expected
    assert len(relay.actions) == 2
    assert not brain.duplicate_clear
    assert not failures(brain)


def test_repair_uses_current_grid_reference_after_reobservation(tmp_path, record):
    class ChangingGridRelay(FakeRelay):
        def observe(self):
            observation = super().observe()
            if len(self.actions) >= 2:
                current_ref = f"e{1000 + self.calls}"
                for item in observation.elements:
                    if item.ref == "e4":
                        item.ref = current_ref
                    if item.parent_ref == "e4":
                        item.parent_ref = current_ref
            return observation

    def current_grid(prompt):
        summary = json.loads(prompt.split("\nUI_TEXT=", 1)[1])
        grid = next(item for item in summary["elements"] if item["role"] == "DataGrid")
        assert grid["ref"] != "e4"
        return clear_decision(grid["ref"])

    result, brain, relay, _ = run_brain(
        tmp_path,
        record,
        [*query_steps(), clear_decision("e4"), current_grid, decision("stop", intent="stop")],
        relay=ChangingGridRelay(record),
    )
    assert result.reason == "model_requested_stop"
    assert brain.duplicate_clear
    assert len(failures(brain)) == 1
    assert len(relay.actions) == 2
    verified = [
        event
        for event in brain.log.read_all()
        if event.kind == "verify" and event.payload.get("check") == "duplicate_clear"
    ]
    assert len(verified) == 1
    assert verified[0].payload["grid_ref"] != "e4"
