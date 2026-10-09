"""Credential submission is bounded input delivery, never proof of authentication."""

from __future__ import annotations

import json

import pytest
from test_brain import FakeRelay, ScriptedModel, decision, element, text_choice
from test_brain import record as record

from brain.graph.engine import Brain
from brain.settings import BrainSettings
from sawon_runtime.storage import RunLog

USERNAME = "synthetic-account"
PASSWORD = "fixture-password-never-observed-481"


class LoginRelay(FakeRelay):
    """A password control whose observed value remains hidden after every input."""

    def __init__(self, record, *, accept_login=True, invalidate=None):
        super().__init__(record)
        self.mode = "login"
        self.user_value = ""
        self.window_handle = 1
        self.password_deliveries = 0
        self.username_deliveries = 0
        self.login_count = 0
        self.authenticated = False
        self.accept_login = accept_login
        self.invalidate = invalidate
        self.invalidated = False

    def observe(self):
        observation = super().observe()
        if self.calls >= 3 and not self.invalidated:
            if self.invalidate == "window":
                self.window_handle = 2
            elif self.invalidate == "username_clear":
                self.user_value = ""
            self.invalidated = True
        window = observation.active_window.model_copy(update={"handle": self.window_handle})
        observation.active_window = window
        observation.windows = [window]
        if self.mode == "login":
            observation.elements.extend(
                [
                    element("e14", "Edit", name="Account", value=self.user_value, parent="e0"),
                    element("e15", "Button", name="Sign in", parent="e0"),
                ]
            )
            assert next(item for item in observation.elements if item.ref == "e8").value is None
        return observation

    def act(self, action):
        item = action.root
        if item.type in {"set_text", "type_text"}:
            if item.target.ref == "e14":
                self.user_value = item.text
                self.username_deliveries += 1
            elif item.target.ref == "e8":
                assert item.text == PASSWORD
                self.password_deliveries += 1
        elif item.type == "click" and item.target.ref == "e15":
            self.login_count += 1
            self.authenticated = bool(
                self.accept_login and self.user_value == USERNAME and self.password_deliveries
            )
            if self.authenticated:
                self.mode = "query"
        return super().act(action)


def credential(source):
    return decision(
        kind="login",
        intent="credential",
        action=text_choice(source, "e14" if source == "credential.username" else "e8"),
    )


def task_state(prompt):
    return json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])


def run_login(tmp_path, record, relay, decisions):
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="credential-test")
    model = ScriptedModel(log, decisions)
    brain = Brain(
        record=record,
        goal="Enter a synthetic record and save for approval",
        relay=relay,
        model=model,
        settings=BrainSettings(),
        case_dir=tmp_path,
        run_log=log,
        username=USERNAME,
        password=PASSWORD,
    )
    return brain.run(), brain, model


@pytest.mark.parametrize("accept_login", [True, False])
def test_hidden_password_submission_guides_login_without_claiming_authentication(
    tmp_path, record, accept_login
):
    relay = LoginRelay(record, accept_login=accept_login)

    def supply_password(prompt):
        state = task_state(prompt)
        assert state["credential_inputs_submitted"] == ["credential.username"]
        assert not relay.authenticated
        return credential("credential.password")

    def activate_login(prompt):
        state = task_state(prompt)
        assert set(state["credential_inputs_submitted"]) == {
            "credential.username",
            "credential.password",
        }
        assert "intent=login" in state["phase_instruction"]
        assert not relay.authenticated
        ui = json.loads(prompt.split("\nUI_TEXT=", 1)[1])
        password = next(item for item in ui["elements"] if item["ref"] == "e8")
        assert password["value"] is None
        login = next(item for item in ui["elements"] if item["name"] == "Sign in")
        return decision(kind="login", intent="login", action={"type": "click", "ref": login["ref"]})

    def stop_after_login(prompt):
        assert relay.authenticated is accept_login
        state = task_state(prompt)
        if accept_login:
            assert state["credential_inputs_submitted"] == []
        else:
            assert set(state["credential_inputs_submitted"]) == {
                "credential.username",
                "credential.password",
            }
        return decision("stop", intent="stop", kind="query" if accept_login else "login")

    result, brain, model = run_login(
        tmp_path,
        record,
        relay,
        [credential("credential.username"), supply_password, activate_login, stop_after_login],
    )
    assert result.reason == "model_requested_stop"
    assert relay.username_deliveries == relay.password_deliveries == relay.login_count == 1
    assert not brain.verified
    assert not brain.save_issued
    assert all(PASSWORD not in prompt for prompt in model.prompts)
    for path in tmp_path.rglob("*.json*"):
        assert PASSWORD not in path.read_text(encoding="utf-8"), path.name
    checks = [event.payload for event in brain.log.read_all() if event.kind == "verify"]
    password_checks = [item for item in checks if item.get("field") == "credential.password"]
    assert len(password_checks) == 1
    assert password_checks[0]["check"] == "protected_input_reread"


@pytest.mark.parametrize(
    "invalidate,source",
    [("window", "credential.password"), ("username_clear", "credential.username")],
)
def test_changed_window_or_cleared_username_allows_fresh_submission(
    tmp_path, record, invalidate, source
):
    relay = LoginRelay(record, invalidate=invalidate)

    def resubmit_in_current_state(prompt):
        assert task_state(prompt)["credential_inputs_submitted"] == []
        return credential(source)

    result, brain, _ = run_login(
        tmp_path,
        record,
        relay,
        [
            credential(source),
            resubmit_in_current_state,
            decision("stop", intent="stop", kind="login"),
        ],
    )
    assert result.reason == "model_requested_stop"
    assert relay.password_deliveries + relay.username_deliveries == 2
    assert not relay.authenticated
    assert not any(
        event.payload.get("reason") == "credential_already_submitted"
        for event in brain.log.read_all()
    )
