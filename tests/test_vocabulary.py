"""Shared GUI vocabulary stays identical to the pre-consolidation contracts.

Intent schema fixtures were captured from pre-consolidation commit 6ab16c3.
ScreenMatch/ScreenPlan were intentionally updated for semantic coordinate mapping on 2026-10-12.
"""

import hashlib
import json
from pathlib import Path

import pytest

from brain.graph.matching import (
    ActionCapability,
    ActionCompletion,
    MatchAction,
    ScreenMatch,
    ScreenPlan,
    TargetedMatchAction,
)
from brain.graph.save_review import SaveReviewResponse
from brain.graph.schemas import Decision, DecisionResponse, KeyChoice, WireActDecision
from brain.graph.screen_batch import LoginScreenPlan, ScreenFillPlan
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


def test_key_contract_schemas_match_preconsolidation_json_and_order():
    assert _schema_digest(KeyChoice) == (
        "ad74921e277d1127dbfd069fd1e5d35c52cd87b4e10553b4b3973dd2740c88c5"
    )
    assert _schema_digest(MatchAction) == (
        "e1141149898cddcf1a0dcd4019971be67d87c8038d77ee946b7ac9f26309e59b"
    )


def test_navigation_keys_preserve_the_existing_tuple_subset():
    assert isinstance(NAVIGATION_KEY_INPUTS, tuple)
    assert NAVIGATION_KEY_INPUTS == tuple(key for key in KEY_INPUT_VALUES if key != "enter")


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
        (ScreenMatch, "aa27cac19816bb42640fedea61ea441f00a95824c06634441ddb2c0aeefe1ad7"),
    ],
)
def test_control_role_schemas_match_recorded_json_and_order(model, digest):
    payload = json.dumps(
        model.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    fixture = (_SCHEMA_FIXTURES / f"{model.__name__}.schema.json").read_text(encoding="utf-8")
    assert payload == fixture
    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == digest


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
        (ActionCapability, "b4169d25a010e0a3e3979cffe29a8c38e73e1c328f52e298d364d32412fc7d06"),
        (MatchAction, "e1141149898cddcf1a0dcd4019971be67d87c8038d77ee946b7ac9f26309e59b"),
        (
            TargetedMatchAction,
            "75145be6d606bba3fb0115feda23d5222595748319343efbe7b67308fa4ea259",
        ),
        (
            ActionCompletion,
            "382b47754e866a0ea5883240fcbf14f7a714090510376c9e7c12e4b6da1ee4e1",
        ),
        (ScreenPlan, "aa27cac19816bb42640fedea61ea441f00a95824c06634441ddb2c0aeefe1ad7"),
        (ScreenMatch, "aa27cac19816bb42640fedea61ea441f00a95824c06634441ddb2c0aeefe1ad7"),
        (Decision, "2fcaf3300563865b937899768e96893b5de15ede16e9e71f926926b6994d77e5"),
        (
            WireActDecision,
            "efb06160b99057fe6b6f1e96bb70cd9955b2830292d151e1e482f0c2a51aae50",
        ),
        (
            DecisionResponse,
            "48c940b67a75445c4a1e83dcc61ae6ca91cd39b675d26edb2d6c86a6e3716bb6",
        ),
        (
            SaveReviewResponse,
            "2031c643fb515d71ebe135f5753f117f08124c416dfc41175ba23f16d68cf71e",
        ),
        (
            ScreenFillPlan,
            "d9aae053bdb9ed55358132dafc40ea6ab1a1ceaf0420d892b7f0d317ca5945a2",
        ),
        (
            LoginScreenPlan,
            "2286447ae531c3ed16b84a669fb924987f5700e4de8bfa392fcb26128d49d0d0",
        ),
        (
            ActionTransition,
            "c404fb5002795b76bd371d98f05b532a6f9cc039addc891d5faf90dab905dda6",
        ),
    ],
)
def test_action_intent_schemas_match_recorded_json_and_order(model, digest):
    payload = json.dumps(
        model.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    fixture = (_SCHEMA_FIXTURES / f"{model.__name__}.schema.json").read_text(encoding="utf-8")
    assert payload == fixture
    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == digest
