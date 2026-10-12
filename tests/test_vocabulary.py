"""Shared GUI vocabulary stays identical to the pre-consolidation contracts."""

import hashlib
import json

from brain.graph.matching import MatchAction
from brain.graph.schemas import KeyChoice
from brain.vocabulary import KEY_INPUT_VALUES, NAVIGATION_KEY_INPUTS


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
