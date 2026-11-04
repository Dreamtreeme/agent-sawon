"""Action delivery is distinct from verified effects and business progress."""

import pytest
from case_labels import labeled_case
from pydantic import ValidationError

from brain.graph.transitions import (
    ActionTransition,
    ExpectedEffect,
    ObservedEffect,
)


def pending_transition(**overrides):
    return ActionTransition(
        **{
            "action_id": "action-1",
            "before_obs_id": "observation-1",
            "intent": "credential",
            "source": "credential.password",
            "expected_effect": ExpectedEffect.PROTECTED_INPUT_REREAD,
            **overrides,
        }
    )


def test_delivered_password_is_not_verified_or_authenticated():
    pending = pending_transition()
    assert pending.delivery == "delivered"
    assert pending.observed_effect == ObservedEffect.UNCHECKED
    assert pending.after_obs_id is None
    assert pending.new_facts == ()
    complete = pending.complete(
        "observation-2",
        ObservedEffect.PROTECTED_INPUT_REREAD,
        ("credential.password_submitted",),
    )
    assert complete.new_facts == ("credential.password_submitted",)
    assert complete.observed_effect != ObservedEffect.INPUT_VALUE_MATCHES
    assert pending.after_obs_id is None
    assert "authenticated" not in complete.model_dump_json()


def test_changed_observation_does_not_create_business_proof():
    pending = pending_transition(
        intent="login", source=None, expected_effect=ExpectedEffect.OBSERVATION_ONLY
    )
    complete = pending.complete("observation-2", ObservedEffect.CHANGED)
    assert complete.new_facts == ()


def test_transition_rejects_each_unverified_or_secret_data_channel():
    # These hit separate guards: effect/observation ordering, fact ordering,
    # source/fact vocabularies, and the extra-field boundary.
    cases = {
        "effect-before-observation": {"observed_effect": ObservedEffect.CHANGED},
        "after-observation-before-completion": {"after_obs_id": "observation-2"},
        "facts-before-completion": {"new_facts": ("save_issued",)},
        "secret-source": {"source": "actual secret value"},
        "secret-fact": {"new_facts": ("actual-secret-value",)},
        "unknown-secret-field": {"password": "actual-secret-value"},
    }
    for case, overrides in cases.items():
        with labeled_case(case):
            with pytest.raises(ValidationError) as raised:
                pending_transition(**overrides)
            assert raised.value.errors()


def test_transition_accepts_a_valid_runtime_task_key():
    transition = pending_transition(
        intent="fill",
        source="invoice.number",
        expected_effect=ExpectedEffect.INPUT_VALUE_MATCHES,
    )

    assert transition.source == "invoice.number"


def test_completed_transition_requires_checked_observation():
    with pytest.raises(ValidationError):
        pending_transition().complete("observation-2", ObservedEffect.UNCHECKED)


def test_protected_input_cannot_claim_value_verification():
    with pytest.raises(ValidationError):
        pending_transition().complete("observation-2", ObservedEffect.INPUT_VALUE_MATCHES)
