"""Shared GUI vocabulary stays identical to the pre-consolidation contracts."""

import hashlib
import json
from pathlib import Path

import pytest

from brain.graph.matching import MatchAction, ScreenMatch
from brain.graph.schemas import KeyChoice
from brain.visual.scene import ControlMeaning, Detection, SemanticDetection, VisualControl
from brain.vocabulary import (
    ACTION_CONTROL_ROLES,
    CONTROL_ROLE_VALUES,
    KEY_INPUT_VALUES,
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
        (ScreenMatch, "847c6acfe7362c7e9faa445d6353d989f47e07e2b83f9d505a6b61a1d10dbaea"),
    ],
)
def test_control_role_schemas_match_preconsolidation_json_and_order(model, digest):
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
