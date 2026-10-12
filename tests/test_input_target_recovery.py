"""A rejected text target can be reselected before dispatch, under fixed bounds."""

import pytest
from case_labels import labeled_case
from pydantic import ValidationError
from test_brain import (
    FakeRelay,
    decision,
    text_choice,
)
from test_brain import record as record

from brain.graph.action_preparation import ActionGuardRequest, validate_action_guard
from brain.graph.engine import GuardStop
from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.graph.schemas import Decision
from brain.settings import BrainSettings


def bad_input(*, kind="query", source="hbl_no", ref="e2"):
    return decision(
        kind=kind,
        intent="query_input" if kind == "query" else "fill",
        action=text_choice(source, ref),
    )


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


def test_retry_limit_has_both_lower_and_upper_bounds():
    for limit in (-1, 4):
        with labeled_case(limit):
            with pytest.raises(ValidationError) as raised:
                BrainSettings(
                    screen_matching=False,
                    max_input_target_retries=limit,
                )
            assert raised.value.errors()
