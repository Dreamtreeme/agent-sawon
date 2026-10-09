"""Provider JSON schema must reject the same malformed output contracts locally."""

from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError

from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.graph.schemas import DecisionResponse


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


def test_wire_schema_accepts_every_supported_action_and_result_branch():
    cases = [
        ("click", decision(action={"type": "click", "ref": "e1"})),
        (
            "text",
            decision(
                intent="query_input", action={"type": "set_text", "ref": "e1", "source": "hbl_no"}
            ),
        ),
        (
            "key",
            decision(intent="search", action={"type": "key", "ref": "e1", "keys": "enter"}),
        ),
        ("wait", decision(intent="wait", action={"type": "wait", "duration_ms": 0})),
        ("focus", decision(action={"type": "focus_window", "handle": 99})),
        (
            "option-source",
            decision(
                intent="select_option",
                action={"type": "select_option", "ref": "e1", "source": "packages.unit"},
            ),
        ),
        (
            "option-name",
            decision(
                intent="select_option",
                action={"type": "select_option", "ref": "e1", "option": "CT"},
            ),
        ),
        ("stop", decision("stop", "stop")),
        ("duplicate", decision("duplicate_clear", "verify", grid_ref="e2")),
        ("complete", complete()),
    ]
    schema = DecisionResponse.model_json_schema()
    for case, payload in cases:
        envelope = {"decision": payload}
        assert schema_accepts(envelope, schema), case
        response = DecisionResponse.model_validate_json(json.dumps(envelope))
        internal = response.to_decision()
        assert internal.operation == payload["operation"], case
        assert internal.intent == payload["intent"], case
        assert (internal.action is None) == (payload["action"] is None), case
        if internal.action is not None:
            assert internal.action.type == payload["action"]["type"], case


def test_wire_schema_and_local_validation_reject_each_invalid_contract_shape():
    cases = [
        (
            "observed-stop-verify",
            decision("stop", "verify", evidence=[{"field": "hbl_no", "ref": "e4"}]),
        ),
        ("act-verify-null", decision(intent="verify")),
        ("act-verify-click", decision(intent="verify", action={"type": "click", "ref": "e1"})),
        ("act-stop-click", decision(intent="stop", action={"type": "click", "ref": "e1"})),
        ("act-null", decision()),
        ("missing-ref", decision(action={"type": "click"})),
        ("null-ref", decision(action={"type": "click", "ref": None})),
        ("blank-ref", decision(action={"type": "click", "ref": "  "})),
        ("missing-source", decision(action={"type": "set_text", "ref": "e1"})),
        ("null-source", decision(action={"type": "type_text", "ref": "e1", "source": None})),
        ("missing-key", decision(action={"type": "key", "ref": "e1"})),
        ("missing-handle", decision(action={"type": "focus_window"})),
        ("missing-option", decision(action={"type": "select_option", "ref": "e1"})),
        ("wait-too-long", decision(intent="wait", action={"type": "wait", "duration_ms": 3001})),
        ("stop-with-action", decision("stop", "stop", action={"type": "click", "ref": "e1"})),
        ("complete-with-action", {**complete(), "action": {"type": "click", "ref": "e1"}}),
        ("missing-grid", {**complete(), "grid_ref": None}),
        ("blank-row", {**complete(), "row_ref": " "}),
        ("duplicate-without-grid", decision("duplicate_clear", "verify")),
    ]
    schema = DecisionResponse.model_json_schema()
    for case, payload in cases:
        envelope = {"decision": payload}
        assert not schema_accepts(envelope, schema), case
        with pytest.raises(ValidationError) as raised:
            DecisionResponse.model_validate_json(json.dumps(envelope))
        assert raised.value.errors(), case


def test_provider_schema_does_not_accept_raw_unwrapped_response():
    payload = decision("stop", "stop")
    schema = DecisionPolicy(ExecutionFacts("final_query")).response_schema()
    assert not schema_accepts(payload, schema.model_json_schema())
    with pytest.raises(ValidationError):
        schema.model_validate_json(json.dumps(payload))


def test_unrelated_operations_do_not_offer_nullable_grid_fields():
    response = DecisionPolicy(
        ExecutionFacts("final_query", duplicate_clear=True, save_issued=True)
    ).response_schema()
    schema = response.model_json_schema()
    for operation, intent in (("act", "navigate"), ("stop", "stop")):
        action = {"type": "click", "ref": "e1"} if operation == "act" else None
        payload = decision(operation, intent, action)
        internal = response.model_validate({"decision": payload}).to_decision()
        assert internal.grid_ref is None and internal.row_ref is None
        for field in ("grid_ref", "row_ref"):
            incompatible = {"decision": {**payload, field: None}}
            assert not schema_accepts(incompatible, schema), (operation, field)
            with pytest.raises(ValidationError):
                response.model_validate(incompatible)


def test_provider_schema_uses_documented_enum_for_single_literal_branches():
    schema = DecisionResponse
    exported = schema.model_json_schema()
    assert '"const"' not in json.dumps(exported)
    stop = exported["$defs"]["WireStopDecision"]["properties"]
    assert stop["operation"]["enum"] == ["stop"]
    assert stop["intent"]["enum"] == ["stop"]
    invalid = {"decision": decision("duplicate_clear", "verify")}
    assert not schema_accepts(invalid, exported)
    with pytest.raises(ValidationError):
        schema.model_validate(invalid)
