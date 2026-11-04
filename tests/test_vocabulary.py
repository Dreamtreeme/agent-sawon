"""Shared GUI vocabulary stays identical to the pre-consolidation contracts.

ScreenFillPlan now carries credential inputs and an optional action; LoginScreenPlan is removed.
ScreenMatch/ScreenPlan add a current-model route resumption group (2026-11-02).
ScreenMatch/ScreenPlan preserve table state, named parents and focus in observations (2026-11-02).
ScreenMatch/ScreenPlan describe accumulated screen-tree observations (2026-11-04).
Task completion annotations are optional for model-owned completion (2026-11-04).
ScreenMatch/ScreenPlan remove the unused provider evidence list (2026-10-29).
ScreenMatch/ScreenPlan separate pointer, keyboard and scroll actions (2026-10-28).
ScreenMatch/ScreenPlan embed source in fields, removing assignments (2026-10-27).

ScreenPlan/ScreenMatch fixtures add screen_path for node-scoped memory (2026-10-24).
ActionTransition adds an unknown structural effect on 2026-10-26; intents are unchanged.
ScreenMatch/ScreenPlan add wheel-key documentation on 2026-10-26; values are unchanged.
MatchAction-family point descriptions distinguish wheel regions on 2026-10-26.
Intent schema fixtures were captured from pre-consolidation commit 6ab16c3.
Decision-family fixtures now describe capture-bound physical input (2026-10-24).
ScreenMatch/ScreenPlan were updated on 2026-10-21 to expose needs_refinement for scanning fallback.
On 2026-10-24 they also expose observed_step_results; vocabulary is unchanged.
"""

import hashlib
import json
from pathlib import Path

import pytest

from brain.graph.save_review import SaveReviewResponse
from brain.graph.schemas import Decision, DecisionResponse, WireActDecision
from brain.graph.screen_batch import ScreenFillPlan
from brain.graph.screen_decision import (
    ActionCapability,
    MatchAction,
    ScreenMatch,
    ScreenPlan,
    TargetedMatchAction,
)
from brain.graph.transitions import ActionTransition
from brain.visual.scene import ControlMeaning, Detection, SemanticDetection, VisualControl
from brain.vocabulary import (
    ACTION_CAPABILITY_INTENT_VALUES,
    ACTION_CONTROL_ROLES,
    ACTION_INTENT_VALUES,
    CONTROL_ROLE_VALUES,
    DECISION_INTENT_VALUES,
    EXECUTABLE_INTENT_VALUES,
    KEY_INPUT_VALUES,
    MATCH_ACTION_INTENT_VALUES,
    NAVIGATION_KEY_INPUTS,
)

_SCHEMA_FIXTURES = Path(__file__).with_name("fixtures") / "vocabulary"


def test_navigation_keys_preserve_the_existing_tuple_subset():
    assert isinstance(NAVIGATION_KEY_INPUTS, tuple)
    assert NAVIGATION_KEY_INPUTS == tuple(key for key in KEY_INPUT_VALUES if key != "enter")


def test_action_control_roles_keep_existing_keys_sets_and_registered_values():
    assert tuple(ACTION_CONTROL_ROLES) == ("fill", "click", "double_click", "key")
    assert all(isinstance(roles, set) for roles in ACTION_CONTROL_ROLES.values())
    assert set().union(*ACTION_CONTROL_ROLES.values()) <= set(CONTROL_ROLE_VALUES)
    with pytest.raises(KeyError):
        ACTION_CONTROL_ROLES["unregistered"]


def test_action_intent_contracts_keep_existing_ordered_tuple_subsets():
    assert all(
        isinstance(values, tuple)
        for values in (
            ACTION_CAPABILITY_INTENT_VALUES,
            MATCH_ACTION_INTENT_VALUES,
            DECISION_INTENT_VALUES,
            EXECUTABLE_INTENT_VALUES,
        )
    )
    assert ACTION_CAPABILITY_INTENT_VALUES == (
        "login",
        "navigate",
        "query_input",
        "fill",
        "search",
        "save",
        "confirm_save",
        "select_tab",
        "select_option",
        "cancel",
        "wait",
        "complete",
        "inspect",
    )
    assert MATCH_ACTION_INTENT_VALUES == tuple(
        intent for intent in EXECUTABLE_INTENT_VALUES if intent != "wait"
    )
    assert DECISION_INTENT_VALUES == (*EXECUTABLE_INTENT_VALUES, "verify", "stop")
    assert all(
        set(values) <= set(ACTION_INTENT_VALUES)
        for values in (
            ACTION_CAPABILITY_INTENT_VALUES,
            MATCH_ACTION_INTENT_VALUES,
            DECISION_INTENT_VALUES,
            EXECUTABLE_INTENT_VALUES,
        )
    )


@pytest.mark.parametrize(
    ("model", "digest"),
    [
        (ControlMeaning, "3f15cd8cd1a1feb8bca4fe7afe81c98069ca8081f0467c4659483ec3cff43c6e"),
        (VisualControl, "44b8a87d6caa5c94fa9ff1674ad5a267725d98afe3b22a3b0aeff222dd120a5e"),
        (Detection, "0e6893dbc589bc2911076bb4e7a08b672036167373240ad063f3a28d1f09182c"),
        (
            SemanticDetection,
            "4bb02cfe9fbffb82400d576b99007a631014b3ce54e807dae954cc07a29caa26",
        ),
        (ActionCapability, "b4169d25a010e0a3e3979cffe29a8c38e73e1c328f52e298d364d32412fc7d06"),
        (MatchAction, "e5247c320d25596a20050602b7627698974052fcb178bff6e158e7f7c16af5ef"),
        (
            TargetedMatchAction,
            "010d16ae1c205f2e97b7661173dd1ff9f35ba34aa17a3d6b0a4165447c6f755c",
        ),
        (ScreenPlan, "4f981e6cf3c80960c847e52a8843ee2672407526c3f3c65d0de7f30a61ff8bf6"),
        (ScreenMatch, "4f981e6cf3c80960c847e52a8843ee2672407526c3f3c65d0de7f30a61ff8bf6"),
        (Decision, "0dd9a48fb753c30ea6f54d65c1272bfc631435abedc33d3864e138bf412f5bf0"),
        (
            WireActDecision,
            "934351453c32a3e6e2dd9ed009242bf1821962426e2525c5c81dc84dc3a9f1a8",
        ),
        (
            DecisionResponse,
            "7410a47133139cd431807ef8df528fe3fe8c69b90e64a6603c68b20e8ac8083f",
        ),
        (
            SaveReviewResponse,
            "ff43cc8625bb9fe58a15c07ff84aa18e8ffe6b797e0aa44b71144d9c76541895",
        ),
        (
            ScreenFillPlan,
            "70458ea40977368a8738b30c4097f5eb3190ab2d9b6e3a5ebcbe7c22c0adedde",
        ),
        (
            ActionTransition,
            "5642cfb971fb5602a6725895b10ea6579aeacf50d04a4f620c1f5f0b2dc6b1ad",
        ),
    ],
)
def test_vocabulary_schemas_match_recorded_json_and_order(model, digest):
    payload = json.dumps(
        model.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    fixture = (_SCHEMA_FIXTURES / f"{model.__name__}.schema.json").read_text(encoding="utf-8")
    assert payload == fixture
    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == digest
