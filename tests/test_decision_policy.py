"""The provider and execution boundary share one state-dependent action contract."""

import json
from dataclasses import FrozenInstanceError, replace

import pytest
from pydantic import ValidationError
from test_wire_contract import schema_accepts

from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.graph.schemas import Decision, DecisionResponse


def action(intent, *, screen="query", kind="click", **choice):
    target = {} if kind in {"wait", "focus_window"} else {"ref": "current-target"}
    return {
        "screen_kind": screen,
        "operation": "act",
        "intent": intent,
        "subgoal": "Choose the next allowed step",
        "reason": "The current observation supports this target",
        "action": {"type": kind, **target, **choice},
    }


def proof(operation):
    result = {
        "screen_kind": "query",
        "operation": operation,
        "intent": "verify",
        "subgoal": "Check observed result evidence",
        "reason": "The current result contains the submitted proof",
        "action": None,
        "grid_ref": "current-grid",
    }
    if operation == "complete":
        result.update(row_ref="current-row", evidence=[{"field": "hbl_no", "ref": "cell"}])
    return result


def stop(screen="query"):
    return {
        "screen_kind": screen,
        "operation": "stop",
        "intent": "stop",
        "subgoal": "Stop safely",
        "reason": "No supported next step",
        "action": None,
    }


def assert_contract(policy, payload, expected):
    """Independent exported-schema oracle, Pydantic, and runtime must agree."""
    schema = policy.response_schema()
    envelope = {"decision": payload}
    assert schema_accepts(envelope, schema.model_json_schema()) is expected
    assert policy.allows(Decision.model_validate(payload)) is expected
    if expected:
        assert isinstance(schema.model_validate(envelope), DecisionResponse)
        assert schema.model_validate(envelope).to_decision().intent == payload["intent"]
    else:
        with pytest.raises(ValidationError):
            schema.model_validate(envelope)


def test_credential_submission_removes_only_submitted_sources_and_then_offers_login():
    initial = ExecutionFacts(
        phase="duplicate_query",
        available_credential_sources=("credential.username", "credential.password"),
    )
    username = action("credential", screen="login", kind="set_text", source="credential.username")
    password = action("credential", screen="login", kind="type_text", source="credential.password")
    login = action("login", screen="login")
    assert_contract(DecisionPolicy(initial), username, True)
    assert_contract(DecisionPolicy(initial), password, True)
    assert_contract(DecisionPolicy(initial), login, False)
    one = replace(initial, submitted_credentials=("credential.username",))
    assert_contract(DecisionPolicy(one), username, False)
    assert_contract(DecisionPolicy(one), password, True)
    assert_contract(DecisionPolicy(one), login, False)
    ready = replace(initial, submitted_credentials=initial.available_credential_sources)
    assert_contract(DecisionPolicy(ready), username, False)
    assert_contract(DecisionPolicy(ready), password, False)
    assert_contract(DecisionPolicy(ready), login, True)
    assert_contract(
        DecisionPolicy(ready), action("login", screen="login", kind="key", keys="enter"), True
    )
    assert (
        DecisionPolicy(ready).as_prompt()["verified_facts"][
            "credential_submission_is_authentication"
        ]
        is False
    )


@pytest.mark.parametrize("screen", ["menu", "query", "editor", "partner", "dialog", "unknown"])
def test_credential_input_is_never_offered_on_other_claimed_screens(screen):
    policy = DecisionPolicy(
        ExecutionFacts("duplicate_query", available_credential_sources=("credential.password",))
    )
    assert_contract(
        policy,
        action("credential", screen=screen, kind="set_text", source="credential.password"),
        False,
    )


def test_absent_credentials_do_not_offer_a_login_command_or_credential_input():
    policy = DecisionPolicy(ExecutionFacts("duplicate_query"))
    assert_contract(policy, action("login", screen="login"), False)
    assert_contract(
        policy,
        action("credential", screen="login", kind="set_text", source="credential.username"),
        False,
    )
    assert policy.as_prompt()["credential_action_if_login"] is None


@pytest.mark.parametrize("phase", ["duplicate_query", "final_query"])
def test_query_input_then_search_have_disjoint_preconditions(phase):
    facts = ExecutionFacts(
        phase, duplicate_clear=phase == "final_query", save_issued=phase == "final_query"
    )
    text = action("query_input", kind="set_text", source="hbl_no")
    search = action("search")
    assert_contract(DecisionPolicy(facts), text, True)
    assert_contract(DecisionPolicy(facts), search, False)
    verified = replace(facts, query_value_verified=True)
    assert_contract(DecisionPolicy(verified), text, False)
    assert_contract(DecisionPolicy(verified), search, True)
    assert_contract(DecisionPolicy(verified), action("search", kind="key", keys="enter"), True)
    searched = replace(verified, query_submitted=True)
    assert_contract(DecisionPolicy(searched), text, False)
    assert_contract(DecisionPolicy(searched), search, False)


def test_duplicate_checkpoint_freezes_ui_until_result_evidence_or_stop():
    before = DecisionPolicy(ExecutionFacts("duplicate_query", query_value_verified=True))
    after = DecisionPolicy(replace(before.facts, query_submitted=True))
    for operation in ("duplicate_clear", "duplicate_found"):
        assert_contract(before, proof(operation), False)
        assert_contract(after, proof(operation), True)
    for payload in (
        action("navigate"),
        action("cancel", kind="key", keys="esc"),
        action("wait", kind="wait"),
    ):
        assert_contract(after, payload, False)
    assert_contract(after, stop(), True)
    assert_contract(after, stop("unknown"), True)
    assert after.current_step == "duplicate_result_evidence"


def test_field_readback_removes_verified_sources_and_prevents_early_save():
    facts = ExecutionFacts("enter", field_names=("hbl_no", "packages.unit"), duplicate_clear=True)
    first = action("fill", screen="editor", kind="set_text", source="hbl_no")
    option = action("select_option", screen="editor", kind="select_option", source="packages.unit")
    save = action("save", screen="editor")
    assert_contract(DecisionPolicy(facts), first, True)
    assert_contract(DecisionPolicy(facts), option, True)
    assert_contract(DecisionPolicy(facts), save, False)
    first_verified = replace(facts, verified_field_names=("hbl_no",))
    assert_contract(DecisionPolicy(first_verified), first, False)
    assert_contract(DecisionPolicy(first_verified), option, True)
    assert_contract(DecisionPolicy(first_verified), save, False)
    ready = replace(facts, verified_field_names=facts.field_names)
    assert_contract(DecisionPolicy(ready), first, False)
    assert_contract(DecisionPolicy(ready), option, False)
    assert_contract(DecisionPolicy(ready), save, True)
    assert_contract(DecisionPolicy(replace(ready, duplicate_clear=False)), save, False)
    assert_contract(DecisionPolicy(replace(ready, save_issued=True)), save, False)
    assert_contract(DecisionPolicy(ExecutionFacts("enter", duplicate_clear=True)), save, False)


def test_partner_selection_and_search_remain_available_during_unfinished_entry():
    policy = DecisionPolicy(ExecutionFacts("enter", field_names=("shipper",), duplicate_clear=True))
    allowed = [
        action("fill", screen="partner", kind="type_text", source="shipper"),
        action("search", screen="partner"),
        action("select_option", screen="partner", kind="double_click"),
        action("select_option", screen="partner", kind="key", keys="enter"),
        action("select_option", screen="partner", kind="select_option", option="Observed option"),
    ]
    for payload in allowed:
        assert_contract(policy, payload, True)
        assert_contract(
            DecisionPolicy(replace(policy.facts, phase="final_query", save_issued=True)),
            payload,
            False,
        )


@pytest.mark.parametrize(
    "missing,value,workflow_ready",
    [
        ("phase", "enter", False),
        ("duplicate_clear", False, False),
        ("save_issued", False, False),
        ("query_value_verified", False, False),
        ("query_submitted", False, False),
        ("final_evidence_ready", False, True),
    ],
)
def test_final_completion_requires_all_prior_runtime_facts(missing, value, workflow_ready):
    ready = ExecutionFacts(
        "final_query",
        duplicate_clear=True,
        save_issued=True,
        query_value_verified=True,
        query_submitted=True,
        final_evidence_ready=True,
    )
    assert ready.final_query_workflow_ready
    incomplete = replace(ready, **{missing: value})
    assert incomplete.final_query_workflow_ready is workflow_ready
    assert_contract(DecisionPolicy(ready), proof("complete"), True)
    assert_contract(DecisionPolicy(incomplete), proof("complete"), False)
    assert_contract(DecisionPolicy(ready), proof("duplicate_clear"), False)


def test_final_proof_can_reveal_current_controls_without_repeating_input_or_search():
    policy = DecisionPolicy(
        ExecutionFacts(
            "final_query",
            duplicate_clear=True,
            save_issued=True,
            query_value_verified=True,
            query_submitted=True,
            final_evidence_ready=True,
        )
    )
    for payload in (
        action("navigate", kind="focus_window", handle=12),
        action("select_tab", screen="editor"),
        action("wait", kind="wait", duration_ms=0),
        action("cancel", screen="dialog", kind="key", keys="esc"),
    ):
        assert_contract(policy, payload, True)
    assert policy.current_step == "final_row_evidence"
    empty_proof = {**proof("complete"), "evidence": []}
    assert_contract(
        policy, empty_proof, True
    )  # The engine independently checks actual field proof.


def test_save_confirmation_requires_a_prior_save_and_a_dialog_activation():
    facts = ExecutionFacts("final_query", duplicate_clear=True)
    payload = action("confirm_save", screen="dialog")
    assert_contract(DecisionPolicy(facts), payload, False)
    issued = DecisionPolicy(replace(facts, save_issued=True))
    assert_contract(issued, payload, True)
    assert_contract(issued, action("confirm_save", screen="query"), False)
    assert_contract(issued, action("confirm_save", screen="dialog", kind="double_click"), False)


@pytest.mark.parametrize(
    "payload,facts,valid",
    [
        pytest.param(payload, None, None, id=f"payload{index}")
        for index, payload in enumerate(
            [
                action("navigate", kind="set_text", source="hbl_no"),
                action("navigate", kind="select_option", source="hbl_no"),
                action("navigate", kind="key", keys="enter"),
                action("navigate", kind="wait"),
                action("search", kind="double_click"),
                action("search", kind="set_text", source="hbl_no"),
                action("query_input", kind="click"),
                action("query_input", kind="set_text", source="mbl_no"),
                action("fill", screen="editor", kind="click"),
                action("wait", kind="click"),
                action("cancel", kind="set_text", source="hbl_no"),
                action("navigate", screen="unknown"),
            ]
        )
    ]
    + [
        pytest.param(
            action("search", kind="wait"),
            ExecutionFacts("duplicate_query", query_value_verified=True),
            action("search"),
            id="wait-as-eligible-search",
        ),
        pytest.param(
            action("save", screen="editor", kind="wait"),
            ExecutionFacts(
                "enter",
                field_names=("hbl_no",),
                verified_field_names=("hbl_no",),
                duplicate_clear=True,
            ),
            action("save", screen="editor"),
            id="wait-as-eligible-save",
        ),
        pytest.param(
            action("confirm_save", screen="dialog", kind="wait"),
            ExecutionFacts("save_result", duplicate_clear=True, save_issued=True),
            action("confirm_save", screen="dialog"),
            id="wait-as-eligible-confirm-save",
        ),
        pytest.param(
            action("login", screen="login", kind="wait"),
            ExecutionFacts(
                "duplicate_query",
                available_credential_sources=("credential.username", "credential.password"),
                submitted_credentials=("credential.username", "credential.password"),
            ),
            action("login", screen="login"),
            id="wait-as-eligible-login",
        ),
    ],
)
def test_broad_action_types_cannot_bypass_intent_or_source_contract(payload, facts, valid):
    if facts is not None:
        policy = DecisionPolicy(facts)
        assert_contract(policy, valid, True)
        assert_contract(policy, payload, False)
        return
    for facts in (
        ExecutionFacts("duplicate_query"),
        ExecutionFacts("enter", field_names=("hbl_no", "mbl_no"), duplicate_clear=True),
        ExecutionFacts(
            "final_query", duplicate_clear=True, save_issued=True, query_value_verified=True
        ),
    ):
        assert_contract(DecisionPolicy(facts), payload, False)


def test_facts_are_canonical_immutable_and_cannot_contain_values_or_secret_names():
    facts = ExecutionFacts(
        "enter", field_names=("mbl_no", "hbl_no", "hbl_no"), duplicate_clear=True
    )
    assert facts.field_names == ("hbl_no", "mbl_no")
    with pytest.raises(FrozenInstanceError):
        facts.phase = "final_query"
    with pytest.raises(ValueError, match="unsupported names"):
        ExecutionFacts("duplicate_query", available_credential_sources=("private-password-value",))
    with pytest.raises(ValueError, match="belong to this task"):
        ExecutionFacts("enter", field_names=("hbl_no",), verified_field_names=("mbl_no",))
    with pytest.raises(ValueError, match="must be available"):
        ExecutionFacts("duplicate_query", submitted_credentials=("credential.password",))
    policy = DecisionPolicy(facts)
    assert policy.response_schema() is DecisionPolicy(replace(facts)).response_schema()
    assert '"const"' not in json.dumps(policy.response_schema().model_json_schema())


def test_prompt_lists_the_same_reduced_choices_without_inferring_login_screen():
    policy = DecisionPolicy(
        ExecutionFacts(
            "enter",
            field_names=("hbl_no", "mbl_no"),
            verified_field_names=("hbl_no",),
            available_credential_sources=("credential.username", "credential.password"),
            submitted_credentials=("credential.username",),
            duplicate_clear=True,
        )
    )
    prompt = policy.as_prompt()
    assert prompt["current_step"] == "fill_fields"
    assert prompt["remaining_fields"] == ["mbl_no"]
    assert prompt["remaining_credential_sources"] == ["credential.password"]
    assert prompt["credential_action_if_login"] == "credential_input"
    intents = {choice["intent"] for choice in prompt["allowed_next_choices"]}
    assert "fill" in intents and "save" not in intents and "login" not in intents
    assert prompt["verified_facts"]["credential_inputs_submitted"] == ["credential.username"]


def test_task_verified_inputs_remain_available_while_save_becomes_allowed():
    facts = ExecutionFacts(
        "task",
        field_names=("invoice.number", "vendor_code"),
        verified_field_names=("invoice.number", "vendor_code"),
    )
    prompt = DecisionPolicy(facts).as_prompt()
    choices = prompt["allowed_next_choices"]
    fill = next(choice for choice in choices if choice["intent"] == "fill")

    assert prompt["remaining_fields"] == []
    assert fill["action"][0]["source"] == ["invoice.number", "vendor_code"]
    assert any(choice["intent"] == "save" for choice in choices)
