"""Shared GUI vocabulary stays identical to the pre-consolidation contracts.

Locally derived OCR text IDs have no arbitrary count cap (2026-11-08).
Current-focus keyboard actions make key target/ref optional (2026-11-08).
Registered app opening extends window actions and existing decision schemas (2026-11-08).
ScreenMatch/ScreenPlan add target-free window focus and requested result sources (2026-11-08).
Capability task text is deduplicated; decision schema descriptions condensed (2026-11-06).
ScreenMatch/ScreenPlan distinguish action decisions from observation-only schemas (2026-11-05).
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
        (ControlMeaning, "2f3bf1c6b5d7d54eecddb7149309f881e048646fc797b3cd358f985d3ce1f25d"),
        (VisualControl, "faae082c21a2e2b676d26491f362d2c3b81fab5e378f9484ffd486adff0ea858"),
        (Detection, "0e6893dbc589bc2911076bb4e7a08b672036167373240ad063f3a28d1f09182c"),
        (
            SemanticDetection,
            "4bb02cfe9fbffb82400d576b99007a631014b3ce54e807dae954cc07a29caa26",
        ),
        (ActionCapability, "81dd3026b6a843cb3dd757bc3556c9b8f255ecb841c2af5e1c9892100655e10c"),
        (MatchAction, "c2896e29704320a3f581ed22c9e62cd4eb9130b17e8324c1eb64cd657797ef7f"),
        (
            TargetedMatchAction,
            "03403957ede105318d200c173f8a71a82a077a1f3e197ff59f7e7665ce7e947e",
        ),
        (ScreenPlan, "64d687f5379913229c8be04b7dbc561e871293f83c8c5e3967228fcd4be66f13"),
        (ScreenMatch, "64d687f5379913229c8be04b7dbc561e871293f83c8c5e3967228fcd4be66f13"),
        (Decision, "4f361687bf9a556d6439945786cb2f959c502506cdd09be48c800f88645bb028"),
        (
            WireActDecision,
            "5bc044aca3044f927de4f08765bafac6ec274b7a4efaa5bb343f5f16760596e3",
        ),
        (
            DecisionResponse,
            "b520181e74c6113ea70c85acbf3d6599ccda90302a60a79ffe3d542dd9ddfded",
        ),
        (
            SaveReviewResponse,
            "6f5134c53bcf6ea939684db9a4f245b22af4d730d789dcad439f8ba8d496a1ec",
        ),
        (
            ScreenFillPlan,
            "f063c8e6891ff28a3ac9cf5c48190ea9981a43718b4c4babe2b3b4e23306ed1b",
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
