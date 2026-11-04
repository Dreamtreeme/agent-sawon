"""The provider and execution boundary share one state-dependent action contract."""

import json
from dataclasses import FrozenInstanceError, replace

import pytest
from case_labels import labeled_case
from pydantic import ValidationError
from test_brain import record as record
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
        phase="task",
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


def test_credential_input_is_never_offered_on_other_claimed_screens():
    policy = DecisionPolicy(
        ExecutionFacts("task", available_credential_sources=("credential.password",))
    )
    for screen in ("menu", "query", "editor", "partner", "dialog", "unknown"):
        with labeled_case(screen):
            assert_contract(
                policy,
                action("credential", screen=screen, kind="set_text", source="credential.password"),
                False,
            )


def test_absent_credentials_do_not_offer_a_login_command_or_credential_input():
    policy = DecisionPolicy(ExecutionFacts("task"))
    assert_contract(policy, action("login", screen="login"), False)
    assert_contract(
        policy,
        action("credential", screen="login", kind="set_text", source="credential.username"),
        False,
    )
    assert policy.as_prompt()["credential_action_if_login"] is None


def test_partner_selection_and_search_remain_available_during_unfinished_entry():
    policy = DecisionPolicy(ExecutionFacts("task", field_names=("shipper",)))
    allowed = [
        action("fill", screen="partner", kind="type_text", source="shipper"),
        action("search", screen="partner"),
        action("select_option", screen="partner", kind="double_click"),
        action("select_option", screen="partner", kind="key", keys="enter"),
    ]
    for payload in allowed:
        assert_contract(policy, payload, True)


def test_save_confirmation_requires_a_prior_save_and_a_dialog_activation():
    facts = ExecutionFacts("save_result")
    payload = action("confirm_save", screen="dialog")
    assert_contract(DecisionPolicy(facts), payload, False)
    issued = DecisionPolicy(replace(facts, save_issued=True))
    assert_contract(issued, payload, True)
    assert_contract(issued, action("confirm_save", screen="query"), False)
    assert_contract(issued, action("confirm_save", screen="dialog", kind="double_click"), False)


def test_broad_action_types_cannot_bypass_intent_or_source_contract():
    cases = [
        (case_id, payload, None, None)
        for case_id, payload in (
            ("navigate-as-text", action("navigate", kind="set_text", source="hbl_no")),
            ("navigate-as-key", action("navigate", kind="key", keys="enter")),
            ("navigate-as-wait", action("navigate", kind="wait")),
            ("search-as-double-click", action("search", kind="double_click")),
            ("search-as-text", action("search", kind="set_text", source="hbl_no")),
            ("fill-wrong-source", action("fill", kind="set_text", source="unknown_field")),
            ("fill-as-click", action("fill", screen="editor", kind="click")),
            ("wait-as-click", action("wait", kind="click")),
            ("cancel-as-text", action("cancel", kind="set_text", source="hbl_no")),
            ("navigate-on-unknown-screen", action("navigate", screen="unknown")),
        )
    ] + [
        (
            "wait-as-eligible-search",
            action("search", kind="wait"),
            ExecutionFacts("task", field_names=("hbl_no",)),
            action("search"),
        ),
        (
            "wait-as-eligible-save",
            action("save", screen="editor", kind="wait"),
            ExecutionFacts(
                "task",
                field_names=("hbl_no",),
                verified_field_names=("hbl_no",),
            ),
            action("save", screen="editor"),
        ),
        (
            "wait-as-eligible-confirm-save",
            action("confirm_save", screen="dialog", kind="wait"),
            ExecutionFacts("save_result", save_issued=True),
            action("confirm_save", screen="dialog"),
        ),
        (
            "wait-as-eligible-login",
            action("login", screen="login", kind="wait"),
            ExecutionFacts(
                "task",
                available_credential_sources=("credential.username", "credential.password"),
                submitted_credentials=("credential.username", "credential.password"),
            ),
            action("login", screen="login"),
        ),
    ]
    for case_id, payload, facts, valid in cases:
        with labeled_case(case_id):
            if facts is not None:
                policy = DecisionPolicy(facts)
                assert_contract(policy, valid, True)
                assert_contract(policy, payload, False)
                continue
            for current in (
                ExecutionFacts("task", field_names=("hbl_no", "mbl_no")),
                ExecutionFacts("save_result", save_issued=True),
            ):
                assert_contract(DecisionPolicy(current), payload, False)


def test_facts_are_canonical_immutable_and_cannot_contain_values_or_secret_names():
    facts = ExecutionFacts(
        "task",
        field_names=("mbl_no", "hbl_no", "hbl_no"),
        delivered_field_names=("mbl_no", "hbl_no", "hbl_no"),
    )
    assert facts.field_names == ("hbl_no", "mbl_no")
    assert facts.delivered_field_names == ("hbl_no", "mbl_no")
    with pytest.raises(FrozenInstanceError):
        facts.phase = "save_result"
    with pytest.raises(ValueError, match="unsupported names"):
        ExecutionFacts("task", available_credential_sources=("private-password-value",))
    for attribute in ("verified_field_names", "delivered_field_names"):
        with labeled_case(attribute):
            with pytest.raises(ValueError, match="belong to this task"):
                ExecutionFacts("task", field_names=("hbl_no",), **{attribute: ("mbl_no",)})
            with pytest.raises(ValueError, match="unsupported names"):
                ExecutionFacts("task", **{attribute: ("credential.password",)})
    with pytest.raises(ValueError, match="must be available"):
        ExecutionFacts("task", submitted_credentials=("credential.password",))
    policy = DecisionPolicy(facts)
    assert policy.response_schema() is DecisionPolicy(replace(facts)).response_schema()
    assert '"const"' not in json.dumps(policy.response_schema().model_json_schema())


def test_prompt_lists_the_same_reduced_choices_without_inferring_login_screen():
    policy = DecisionPolicy(
        ExecutionFacts(
            "task",
            field_names=("hbl_no", "mbl_no"),
            verified_field_names=("hbl_no",),
            available_credential_sources=("credential.username", "credential.password"),
            submitted_credentials=("credential.username",),
        )
    )
    prompt = policy.as_prompt()
    assert prompt["current_step"] == "task"
    assert prompt["remaining_fields"] == ["mbl_no"]
    assert prompt["remaining_credential_sources"] == ["credential.password"]
    assert prompt["credential_action_if_login"] == "credential_input"
    intents = {choice["intent"] for choice in prompt["allowed_next_choices"]}
    assert "fill" in intents and "save" not in intents and "login" not in intents
    assert prompt["verified_facts"]["credential_inputs_submitted"] == ["credential.username"]


@pytest.mark.parametrize(
    "verified, delivered, save_issued, expected_save, remaining",
    [
        pytest.param(("invoice.number", "vendor_code"), (), False, True, (), id="verified"),
        pytest.param(
            (),
            ("invoice.number", "vendor_code"),
            False,
            True,
            ("invoice.number", "vendor_code"),
            id="delivered",
        ),
        pytest.param(
            ("invoice.number",),
            ("vendor_code",),
            False,
            True,
            ("vendor_code",),
            id="verified-and-delivered",
        ),
        pytest.param(
            (),
            ("invoice.number",),
            False,
            False,
            ("invoice.number", "vendor_code"),
            id="missing-delivery",
        ),
        pytest.param(
            (),
            ("invoice.number", "vendor_code"),
            True,
            False,
            ("invoice.number", "vendor_code"),
            id="already-saved",
        ),
    ],
)
def test_task_inputs_remain_available_and_delivery_allows_save_without_verifying(
    verified, delivered, save_issued, expected_save, remaining
):
    facts = ExecutionFacts(
        "task",
        field_names=("invoice.number", "vendor_code"),
        verified_field_names=verified,
        delivered_field_names=delivered,
        save_issued=save_issued,
    )
    policy = DecisionPolicy(facts)
    prompt = policy.as_prompt()
    choices = prompt["allowed_next_choices"]
    fill = next(choice for choice in choices if choice["intent"] == "fill")

    assert facts.remaining_fields == remaining
    assert prompt["remaining_fields"] == list(remaining)
    assert fill["action"][0]["source"] == ["invoice.number", "vendor_code"]
    assert any(choice["intent"] == "save" for choice in choices) is expected_save
    assert_contract(policy, action("save", screen="editor"), expected_save)
    assert prompt["delivered_fields"] == list(delivered)
    assert "delivered_fields" not in prompt["verified_facts"]
    assert facts.verified_field_names == verified
    assert prompt["verified_facts"]["verified_fields"] == list(verified)
    assert facts.final_evidence_ready is False
    assert prompt["verified_facts"]["final_evidence_ready"] is False


@pytest.mark.parametrize("phase", ["duplicate_query", "enter", "save_result", "final_query"])
def test_delivery_does_not_relax_legacy_save_policy(phase):
    facts = ExecutionFacts(
        phase,
        field_names=("hbl_no",),
        delivered_field_names=("hbl_no",),
        duplicate_clear=True,
    )

    assert facts.remaining_fields == ("hbl_no",)
    assert facts.final_evidence_ready is False
    assert_contract(DecisionPolicy(facts), action("save", screen="editor"), False)


@pytest.mark.parametrize(
    "current_reading, reading_path, redeliver, expected_save",
    [
        pytest.param(None, "Entry", False, True, id="unobserved-delivery"),
        pytest.param("matching", "Entry", False, True, id="matching-observation"),
        pytest.param("mismatch", "Entry", False, False, id="current-mismatch"),
        pytest.param("mismatch", "Other tab", False, False, id="mismatch-on-another-node"),
        pytest.param("mismatch", "Other tab", True, True, id="redelivered-after-mismatch"),
    ],
)
def test_current_observation_supersedes_delivered_save_eligibility(
    tmp_path, record, current_reading, reading_path, redeliver, expected_save
):
    from test_screen_decision import setup_brain, wire

    from brain.graph.screen_decision import ScreenMatch
    from brain.graph.screen_mapping import ScreenMapping
    from brain.graph.screen_memory import remember_input_delivery
    from brain.visual.scene import SceneObservation

    brain, *_ = setup_brain(tmp_path, record, [])
    brain.observation = SceneObservation(wire=wire(), screen_kind="editor", elements=[])
    source = "hbl_no"

    def observe_fields(path, fields, number):
        brain.observation_number = number
        brain._remember_model_task_evidence(
            ScreenMatch(
                screen_kind="editor",
                procedure_step="task-action",
                controls=[],
                mapping=ScreenMapping(screen_path=[path], fields=fields),
            )
        )

    fields = {
        key: {"source": key, "role": "edit", "bbox": [10, 10, 100, 20]} for key in brain.values
    }
    observe_fields("Entry", fields, 1)
    for key, value in brain.values.items():
        remember_input_delivery(
            brain.screen_nodes,
            ["Entry"],
            key,
            value,
            target={"label": key, "role": "edit", "bbox": [10, 10, 100, 20]},
            observation_id=str(brain.observation.obs_id),
            observation_number=1,
        )
    if current_reading is not None:
        value = brain.values[source] if current_reading == "matching" else "WRONG CURRENT VALUE"
        observe_fields(reading_path, {source: {**fields[source], "value": value}}, 2)
    if redeliver:
        remember_input_delivery(
            brain.screen_nodes,
            [reading_path],
            source,
            brain.values[source],
            target={"label": source, "role": "edit", "bbox": [10, 10, 100, 20]},
            observation_id=str(brain.observation.obs_id),
            observation_number=3,
        )

    policy = brain._decision_policy()
    assert (source in policy.facts.delivered_field_names) is (current_reading is None or redeliver)
    assert (source in policy.facts.verified_field_names) is (current_reading == "matching")
    assert policy.facts.final_evidence_ready is False
    assert_contract(policy, action("save", screen="editor"), expected_save)
