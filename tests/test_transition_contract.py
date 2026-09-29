"""Action delivery is distinct from verified effects and business progress."""

import pytest
from pydantic import ValidationError

from brain.graph.transitions import (
    ActionTransition,
    ExpectedEffect,
    NavigationHistory,
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


@pytest.mark.parametrize("intent", ["login", "save", "confirm_save", "search"])
def test_changed_observation_does_not_create_business_proof(intent):
    pending = pending_transition(
        intent=intent, source=None, expected_effect=ExpectedEffect.OBSERVATION_ONLY
    )
    complete = pending.complete("observation-2", ObservedEffect.CHANGED)
    assert complete.new_facts == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"observed_effect": ObservedEffect.CHANGED},
        {"after_obs_id": "observation-2"},
        {"new_facts": ("save_issued",)},
        {"source": "actual-secret-value"},
        {"new_facts": ("actual-secret-value",)},
        {"password": "actual-secret-value"},
        {"control_label": "Untrusted screen text"},
    ],
)
def test_transition_rejects_unverified_effects_and_arbitrary_data(overrides):
    with pytest.raises(ValidationError):
        pending_transition(**overrides)


def test_completed_transition_requires_checked_observation():
    with pytest.raises(ValidationError):
        pending_transition().complete("observation-2", ObservedEffect.UNCHECKED)


def test_protected_input_cannot_claim_value_verification():
    with pytest.raises(ValidationError):
        pending_transition().complete("observation-2", ObservedEffect.INPUT_VALUE_MATCHES)


def test_cycle_detects_abab_and_allows_alternative_progress():
    history = NavigationHistory()
    assert not history.observe("A", "B", "enter:1")
    assert not history.observe("B", "A", "enter:1")
    assert history.observe("A", "B", "enter:1")
    assert history.observe("B", "A", "enter:1")
    assert not history.observe("A", "C", "enter:1")
    assert history.states == ("A", "B", "A", "B", "A", "C")


def test_no_op_actions_do_not_hide_navigation_cycle():
    history = NavigationHistory()
    assert not history.observe("A", "B", "enter:1")
    assert not history.observe("B", "B", "enter:1")
    assert not history.observe("B", "A", "enter:1")
    assert not history.observe("A", "A", "enter:1")
    assert history.observe("A", "B", "enter:1")


def test_longer_cycle_is_detected():
    history = NavigationHistory()
    for before, after in [("A", "B"), ("B", "C"), ("C", "A"), ("A", "B")]:
        assert not history.observe(before, after, "enter:1")
    assert history.observe("B", "C", "enter:1")


@pytest.mark.parametrize("new_checkpoint", ["enter:2", "final_query:1"])
def test_verified_field_or_phase_progress_resets_cycle_scope(new_checkpoint):
    history = NavigationHistory()
    assert not history.observe("A", "B", "enter:1")
    assert not history.observe("B", "A", "enter:1")
    assert not history.observe("A", "B", new_checkpoint)
    assert history.states == ("A", "B")


def test_checkpoint_change_resets_even_during_no_op():
    history = NavigationHistory()
    history.observe("A", "B", "enter:1")
    history.observe("B", "A", "enter:1")
    assert not history.observe("A", "A", "enter:2")
    assert not history.observe("A", "B", "enter:2")


def test_unrecorded_semantic_state_is_preserved_without_erasing_history():
    history = NavigationHistory()
    assert not history.observe("A", "B", "enter:1")
    assert history.observe("A", "B", "enter:1")


def test_run_history_is_bounded_and_not_shared():
    history = NavigationHistory(max_states=8)
    for index in range(30):
        assert not history.observe(str(index), str(index + 1), "enter:1")
    assert history.states == tuple(str(index) for index in range(23, 31))
    assert NavigationHistory().states == ()


@pytest.mark.parametrize("max_states", [0, 3, 65])
def test_cycle_history_has_finite_useful_bounds(max_states):
    with pytest.raises(ValueError):
        NavigationHistory(max_states=max_states)
