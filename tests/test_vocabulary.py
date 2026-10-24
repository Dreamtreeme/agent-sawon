"""Shared GUI vocabulary stays identical to the pre-consolidation contracts.

ScreenPlan/ScreenMatch fixtures add screen_path for node-scoped memory (2026-10-24).
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
from brain.graph.schemas import Decision, DecisionResponse, KeyChoice, WireActDecision
from brain.graph.screen_batch import LoginScreenPlan, ScreenFillPlan
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


def _schema_digest(model) -> str:
    payload = json.dumps(
        model.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_key_choice_schema_matches_preconsolidation_json_and_order():
    assert _schema_digest(KeyChoice) == (
        "ad74921e277d1127dbfd069fd1e5d35c52cd87b4e10553b4b3973dd2740c88c5"
    )


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
        (MatchAction, "e1141149898cddcf1a0dcd4019971be67d87c8038d77ee946b7ac9f26309e59b"),
        (
            TargetedMatchAction,
            "75145be6d606bba3fb0115feda23d5222595748319343efbe7b67308fa4ea259",
        ),
        (ScreenPlan, "c6b9515eccfe5748983de40377eb9e96644a553fb440882348a7fd21e0b90281"),
        (ScreenMatch, "c6b9515eccfe5748983de40377eb9e96644a553fb440882348a7fd21e0b90281"),
        (Decision, "0dd9a48fb753c30ea6f54d65c1272bfc631435abedc33d3864e138bf412f5bf0"),
        (
            WireActDecision,
            "934351453c32a3e6e2dd9ed009242bf1821962426e2525c5c81dc84dc3a9f1a8",
        ),
        (
            DecisionResponse,
            "4d9656f93d9c67fb899b06b47e59fe11cf6da39eb37019c40b05e522afe06559",
        ),
        (
            SaveReviewResponse,
            "df4d869aa37754a0680ee8c431149e73520243c7222fb5fa35008ad09c98f6cc",
        ),
        (
            ScreenFillPlan,
            "901a70c77c434069224da425d0304e43c8b051c39934c8b6f7908ede884f0d3b",
        ),
        (
            LoginScreenPlan,
            "0c3348524763e3f67827f360493ebf424cd2bc8fb91378c8dc001451c06bb258",
        ),
        (
            ActionTransition,
            "c404fb5002795b76bd371d98f05b532a6f9cc039addc891d5faf90dab905dda6",
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
