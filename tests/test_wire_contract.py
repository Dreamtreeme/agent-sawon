"""Provider JSON schema must reject the same malformed output contracts locally."""

from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError

from brain.graph.schemas import (
    DecisionResponse,
    DuplicateResponse,
    FinalQueryPendingResponse,
    FinalQueryResponse,
)


def schema_accepts(value, schema, root=None):
    """Evaluate only the JSON Schema keywords emitted by these contracts.

    This dependency-free test oracle is intentionally limited, not a general
    JSON Schema validator. Unknown keywords fail the test instead of silently
    skipping a new constraint. Runtime validation never uses this helper.
    """
    root = schema if root is None else root
    supported = {
        "$defs",
        "$ref",
        "title",
        "description",
        "default",
        "type",
        "enum",
        "const",
        "anyOf",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
    }
    assert not set(schema) - supported, (
        f"Unsupported test schema keywords: {set(schema) - supported}"
    )
    if "$ref" in schema:
        target = root
        for key in schema["$ref"].removeprefix("#/").split("/"):
            target = target[key.replace("~1", "/").replace("~0", "~")]
        if not schema_accepts(value, target, root):
            return False
    if "anyOf" in schema and not any(
        schema_accepts(value, branch, root) for branch in schema["anyOf"]
    ):
        return False
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    kind = schema.get("type")
    checks = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": type(value) in {int, float},
        "null": value is None,
        "boolean": type(value) is bool,
    }
    if kind is not None and not checks[kind]:
        return False
    if isinstance(value, dict):
        if not set(schema.get("required", [])).issubset(value):
            return False
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        if any(
            not schema_accepts(item, properties[key], root)
            for key, item in value.items()
            if key in properties
        ):
            return False
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", float("inf")):
            return False
        if "items" in schema and not all(
            schema_accepts(item, schema["items"], root) for item in value
        ):
            return False
    if isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", float("inf")):
            return False
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            return False
    if type(value) in {int, float}:
        if not schema.get("minimum", -float("inf")) <= value <= schema.get("maximum", float("inf")):
            return False
    return True


def decision(operation="act", intent="navigate", action=None, **extra):
    return {
        "screen_kind": "query",
        "operation": operation,
        "intent": intent,
        "subgoal": "Synthetic observed task",
        "reason": "Synthetic contract proof",
        "action": action,
        **extra,
    }


def complete():
    return decision(
        "complete",
        "verify",
        grid_ref="e2",
        row_ref="e3",
        evidence=[{"field": "hbl_no", "ref": "e4"}],
    )


@pytest.mark.parametrize(
    "payload",
    [
        decision(action={"type": "click", "ref": "e1"}),
        decision(
            intent="query_input", action={"type": "set_text", "ref": "e1", "source": "hbl_no"}
        ),
        decision(intent="search", action={"type": "key", "ref": "e1", "keys": "enter"}),
        decision(intent="wait", action={"type": "wait", "duration_ms": 0}),
        decision(action={"type": "focus_window", "handle": 99}),
        decision(
            intent="select_option",
            action={"type": "select_option", "ref": "e1", "source": "packages.unit"},
        ),
        decision(
            intent="select_option", action={"type": "select_option", "ref": "e1", "option": "CT"}
        ),
        decision("stop", "stop"),
        decision("duplicate_clear", "verify", grid_ref="e2"),
        complete(),
    ],
    ids=[
        "click",
        "text",
        "key",
        "wait",
        "focus",
        "option-source",
        "option-name",
        "stop",
        "duplicate",
        "complete",
    ],
)
def test_wire_schema_accepts_valid_outputs_and_normalizes_to_internal_decision(payload):
    envelope = {"decision": payload}
    assert schema_accepts(envelope, DecisionResponse.model_json_schema())
    response = DecisionResponse.model_validate_json(json.dumps(envelope))
    internal = response.to_decision()
    assert internal.operation == payload["operation"]
    assert internal.intent == payload["intent"]
    assert (internal.action is None) == (payload["action"] is None)
    if internal.action is not None:
        assert internal.action.type == payload["action"]["type"]


@pytest.mark.parametrize(
    "payload",
    [
        decision("stop", "verify", evidence=[{"field": "hbl_no", "ref": "e4"}]),
        decision(intent="verify"),
        decision(intent="verify", action={"type": "click", "ref": "e1"}),
        decision(intent="stop", action={"type": "click", "ref": "e1"}),
        decision(),
        decision(action={"type": "click"}),
        decision(action={"type": "click", "ref": None}),
        decision(action={"type": "click", "ref": "  "}),
        decision(action={"type": "set_text", "ref": "e1"}),
        decision(action={"type": "type_text", "ref": "e1", "source": None}),
        decision(action={"type": "key", "ref": "e1"}),
        decision(action={"type": "focus_window"}),
        decision(action={"type": "select_option", "ref": "e1"}),
        decision(intent="wait", action={"type": "wait", "duration_ms": 3001}),
        decision("stop", "stop", action={"type": "click", "ref": "e1"}),
        {**complete(), "action": {"type": "click", "ref": "e1"}},
        {**complete(), "grid_ref": None},
        {**complete(), "row_ref": " "},
        decision("duplicate_clear", "verify"),
    ],
    ids=[
        "observed-stop-verify",
        "act-verify-null",
        "act-verify-click",
        "act-stop-click",
        "act-null",
        "missing-ref",
        "null-ref",
        "blank-ref",
        "missing-source",
        "null-source",
        "missing-key",
        "missing-handle",
        "missing-option",
        "wait-too-long",
        "stop-with-action",
        "complete-with-action",
        "missing-grid",
        "blank-row",
        "duplicate-without-grid",
    ],
)
def test_wire_schema_and_local_validation_both_reject_invalid_contracts(payload):
    envelope = {"decision": payload}
    assert not schema_accepts(envelope, DecisionResponse.model_json_schema())
    with pytest.raises(ValidationError):
        DecisionResponse.model_validate_json(json.dumps(envelope))


@pytest.mark.parametrize(
    "schema,allowed",
    [
        (DuplicateResponse, {"duplicate_clear", "duplicate_found", "stop"}),
        (FinalQueryPendingResponse, {"act", "stop"}),
        (FinalQueryResponse, {"act", "complete", "stop"}),
    ],
)
def test_phase_response_schema_limits_operations_without_hidden_validators(schema, allowed):
    choices = [
        decision(action={"type": "click", "ref": "e1"}),
        decision("stop", "stop"),
        decision("duplicate_clear", "verify", grid_ref="e2"),
        decision("duplicate_found", "verify", grid_ref="e2"),
        complete(),
    ]
    exported = schema.model_json_schema()
    assert exported["type"] == "object"
    assert exported["required"] == ["decision"]
    assert "anyOf" in exported["properties"]["decision"]
    assert "discriminator" not in json.dumps(exported)
    for payload in choices:
        envelope = {"decision": payload}
        assert schema_accepts(envelope, exported) == (payload["operation"] in allowed)
        if payload["operation"] in allowed:
            schema.model_validate_json(json.dumps(envelope)).to_decision()
        else:
            with pytest.raises(ValidationError):
                schema.model_validate_json(json.dumps(envelope))


def test_provider_schema_does_not_accept_raw_unwrapped_response():
    payload = decision("stop", "stop")
    assert not schema_accepts(payload, FinalQueryPendingResponse.model_json_schema())
    with pytest.raises(ValidationError):
        FinalQueryPendingResponse.model_validate_json(json.dumps(payload))


def test_schema_oracle_fails_on_unimplemented_keywords():
    with pytest.raises(AssertionError, match="Unsupported test schema keywords"):
        schema_accepts("e1", {"type": "string", "unsupported_test_keyword": True})


@pytest.mark.parametrize("operation,intent", [("act", "navigate"), ("stop", "stop")])
def test_unrelated_operations_do_not_offer_nullable_grid_fields(operation, intent):
    action = {"type": "click", "ref": "e1"} if operation == "act" else None
    payload = decision(operation, intent, action)
    schema = FinalQueryPendingResponse.model_json_schema()
    internal = FinalQueryPendingResponse.model_validate({"decision": payload}).to_decision()
    assert internal.grid_ref is None and internal.row_ref is None
    for field in ("grid_ref", "row_ref"):
        incompatible = {"decision": {**payload, field: None}}
        assert not schema_accepts(incompatible, schema)
        with pytest.raises(ValidationError):
            FinalQueryPendingResponse.model_validate(incompatible)


@pytest.mark.parametrize(
    "schema", [DecisionResponse, DuplicateResponse, FinalQueryPendingResponse, FinalQueryResponse]
)
def test_provider_schema_uses_documented_enum_for_single_literal_branches(schema):
    exported = schema.model_json_schema()
    assert '"const"' not in json.dumps(exported)
    stop = exported["$defs"]["WireStopDecision"]["properties"]
    assert stop["operation"]["enum"] == ["stop"]
    assert stop["intent"]["enum"] == ["stop"]
    invalid = {"decision": decision("duplicate_clear", "verify")}
    assert not schema_accepts(invalid, exported)
    with pytest.raises(ValidationError):
        schema.model_validate(invalid)
