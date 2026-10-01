from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, model_validator
from pydantic_core import PydanticCustomError

from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.runtime.storage import RunLog
from models.base import MeasuredModelClient, ProviderReply, TokenUsage, request_fingerprint
from models.validation import safe_validation_errors


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


class Items(BaseModel):
    items: list[Item]


@pytest.fixture
def final_search_schema():
    return DecisionPolicy(
        ExecutionFacts(
            "final_query", duplicate_clear=True, save_issued=True, query_value_verified=True
        )
    ).response_schema()


def test_nested_schema_fields_and_indices_survive_without_input():
    with pytest.raises(ValidationError) as raised:
        Items.model_validate({"items": [{"value": "PRIVATE_RESPONSE_VALUE"}]})
    assert safe_validation_errors(raised.value, Items) == [
        {"type": "int_parsing", "loc": ["items", 0, "value"]}
    ]


@pytest.mark.parametrize(
    "private_key", ["PRIVATE_API_KEY", "https://host/?key=SECRET", "PRIVATE\nRESPONSE"]
)
def test_extra_field_names_do_not_enter_locations(private_key: str):
    with pytest.raises(ValidationError) as raised:
        Item.model_validate({"value": 1, private_key: "PRIVATE_VALUE"})
    result = safe_validation_errors(raised.value, Item)
    assert result == [{"type": "extra_forbidden", "loc": []}]
    assert private_key not in json.dumps(result)
    assert "PRIVATE_VALUE" not in json.dumps(result)


def test_schema_examples_do_not_add_allowed_field_names():
    class ExampleMetadata(BaseModel):
        model_config = ConfigDict(
            extra="forbid", json_schema_extra={"examples": [{"properties": {"PRIVATE_KEY": {}}}]}
        )
        value: int

    with pytest.raises(ValidationError) as raised:
        ExampleMetadata.model_validate({"value": 1, "PRIVATE_KEY": "PRIVATE_INPUT"})
    assert safe_validation_errors(raised.value, ExampleMetadata) == [
        {"type": "extra_forbidden", "loc": []}
    ]


def test_trusted_union_branch_names_are_removed_without_losing_fields():
    private_branch = create_model("PRIVATE_BRANCH_NAME", value=(int, ...))
    envelope = create_model("Envelope", choice=(private_branch | int, ...))
    with pytest.raises(ValidationError) as raised:
        envelope.model_validate({"choice": {"value": "PRIVATE_VALUE"}})
    result = safe_validation_errors(raised.value, envelope)
    assert len(result) == 2
    assert result[0]["loc"] == ["choice", "value"]
    assert result[1]["loc"] == []  # Primitive union tag "int" is not a declared definition.
    assert "PRIVATE" not in json.dumps(result)


def test_unknown_union_branch_names_still_clear_the_entire_location():
    error = ValidationError.from_exception_data(
        "Envelope",
        [
            {
                "type": "int_parsing",
                "loc": ("items", 0, "PRIVATE_UNKNOWN_BRANCH", "value"),
                "input": "PRIVATE_INPUT",
            }
        ],
    )
    assert safe_validation_errors(error, Items) == [{"type": "int_parsing", "loc": []}]


def test_property_names_take_precedence_over_definition_names():
    branch = create_model("Branch", value=(int, ...))
    envelope = create_model("Envelope", Branch=(branch, ...))
    with pytest.raises(ValidationError) as raised:
        envelope.model_validate({"Branch": {"value": "PRIVATE_INPUT"}})
    assert safe_validation_errors(raised.value, envelope) == [
        {"type": "int_parsing", "loc": ["Branch", "value"]}
    ]


@pytest.mark.parametrize("metadata_key", ["title", "examples", "default"])
def test_schema_annotation_values_do_not_authorize_union_branches(metadata_key: str):
    class Metadata(BaseModel):
        model_config = ConfigDict(json_schema_extra={metadata_key: {"$defs": {"PRIVATE": {}}}})
        value: int

    error = ValidationError.from_exception_data(
        "Metadata", [{"type": "int_parsing", "loc": ("PRIVATE", "value"), "input": "SECRET"}]
    )
    assert safe_validation_errors(error, Metadata) == [{"type": "int_parsing", "loc": []}]


def test_actual_wire_union_preserves_decision_intent_path(final_search_schema):
    payload = {
        "decision": {
            "screen_kind": "query",
            "operation": "stop",
            "intent": "verify",
            "action": None,
            "subgoal": "PRIVATE_SUBGOAL",
            "reason": "PRIVATE_REASON",
        }
    }
    with pytest.raises(ValidationError) as raised:
        final_search_schema.model_validate(payload)
    result = safe_validation_errors(raised.value, final_search_schema)
    assert {"type": "literal_error", "loc": ["decision", "intent"]} in result
    encoded = json.dumps(result)
    assert "Wire" not in encoded
    assert "PRIVATE" not in encoded


def test_actual_nested_wire_union_preserves_action_ref_path(final_search_schema):
    payload = {
        "decision": {
            "screen_kind": "query",
            "operation": "act",
            "intent": "search",
            "action": {"type": "click", "ref": None},
            "subgoal": "PRIVATE_SUBGOAL",
            "reason": "PRIVATE_REASON",
        }
    }
    with pytest.raises(ValidationError) as raised:
        final_search_schema.model_validate(payload)
    result = safe_validation_errors(raised.value, final_search_schema)
    assert {"type": "string_type", "loc": ["decision", "action", "ref"]} in result
    assert "PRIVATE" not in json.dumps(result)


def test_discriminator_values_and_error_context_are_not_logged():
    class SecretBranch(BaseModel):
        kind: Literal["PRIVATE_TAG"]
        value: int

    class OtherBranch(BaseModel):
        kind: Literal["other"]
        value: int

    class Envelope(BaseModel):
        choice: Annotated[SecretBranch | OtherBranch, Field(discriminator="kind")]

    with pytest.raises(ValidationError) as raised:
        Envelope.model_validate({"choice": {"kind": "PRIVATE_TAG", "value": "PRIVATE_VALUE"}})
    assert safe_validation_errors(raised.value, Envelope) == [{"type": "int_parsing", "loc": []}]
    with pytest.raises(ValidationError) as raised:
        Envelope.model_validate({"choice": {"kind": "PRIVATE_UNKNOWN", "value": 1}})
    result = safe_validation_errors(raised.value, Envelope)
    assert result == [{"type": "union_tag_invalid", "loc": ["choice"]}]
    assert "PRIVATE" not in json.dumps(result)


def test_unknown_custom_error_type_message_and_context_are_omitted():
    class PrivateError(BaseModel):
        value: str

        @model_validator(mode="after")
        def reject(self):
            raise PydanticCustomError(
                "PRIVATE_ERROR_TYPE",
                "PRIVATE_MESSAGE {secret}",
                {"secret": "https://host/?key=PRIVATE_KEY"},
            )

    with pytest.raises(ValidationError) as raised:
        PrivateError.model_validate({"value": "PRIVATE_INPUT"})
    result = safe_validation_errors(raised.value, PrivateError)
    assert result == [{"type": "validation_error", "loc": []}]
    assert "PRIVATE" not in json.dumps(result)


def test_fixed_validator_message_maps_to_rule_without_context():
    class KnownRule(BaseModel):
        value: str

        @model_validator(mode="after")
        def reject(self):
            raise ValueError("Non-action decision intent must match its operation")

    with pytest.raises(ValidationError) as raised:
        KnownRule.model_validate({"value": "PRIVATE_INPUT"})
    assert safe_validation_errors(raised.value, KnownRule) == [
        {"type": "value_error", "loc": [], "rule": "nonaction_intent_mismatch"}
    ]


def test_unknown_value_error_message_is_not_a_rule():
    class PrivateRule(BaseModel):
        value: str

        @model_validator(mode="after")
        def reject(self):
            raise ValueError("Non-action decision intent must match its operation PRIVATE_KEY")

    with pytest.raises(ValidationError) as raised:
        PrivateRule.model_validate({"value": "PRIVATE_INPUT"})
    assert safe_validation_errors(raised.value, PrivateRule) == [{"type": "value_error", "loc": []}]


def test_no_schema_omits_string_location():
    with pytest.raises(ValidationError) as raised:
        Item.model_validate({"value": "PRIVATE_VALUE"})
    assert safe_validation_errors(raised.value) == [{"type": "int_parsing", "loc": []}]


def test_invalid_json_does_not_publish_parser_context():
    with pytest.raises(ValidationError) as raised:
        Item.model_validate_json('{"PRIVATE_KEY":')
    assert safe_validation_errors(raised.value, Item) == [{"type": "json_invalid", "loc": []}]


def test_model_validation_metadata_preserves_usage_cost_and_fingerprint(tmp_path: Path):
    class FakeClient(MeasuredModelClient):
        def _generate(self, prompt, schema, *, images):
            return ProviderReply(
                text='{"value":"PRIVATE_RESPONSE","PRIVATE_KEY":"PRIVATE_CONTEXT"}',
                usage=TokenUsage(input_tokens=100, response_tokens=20),
            )

        def _cost(self, usage):
            return Decimal("0.001")

    log = RunLog(tmp_path / "case", case_id="test", agent_id="test")
    client = FakeClient(provider="fake", model="test", run_log=log)
    with pytest.raises(ValidationError):
        client.complete_json("PRIVATE_PROMPT", Item)
    (event,) = log.read_all()
    assert sorted(event.payload["validation_errors"], key=lambda item: item["type"]) == [
        {"type": "extra_forbidden", "loc": []},
        {"type": "int_parsing", "loc": ["value"]},
    ]
    assert event.payload["error_type"] == "ValidationError"
    assert event.payload["status"] == "error"
    assert event.payload["request_sha256"] == request_fingerprint("PRIVATE_PROMPT", Item, None)
    assert event.cost.input_tokens == 100
    assert event.cost.output_tokens == 20
    assert event.cost.usd == pytest.approx(0.001)
    assert "PRIVATE" not in event.model_dump_json()


def test_pre_response_validation_failure_keeps_unknown_cost(tmp_path: Path):
    class FakeClient(MeasuredModelClient):
        def _generate(self, prompt, schema, *, images):
            Item.model_validate({"value": "PRIVATE_SDK_INPUT"})
            raise AssertionError("Unreachable")

        def _cost(self, usage):
            raise AssertionError("No usage was returned")

    log = RunLog(tmp_path / "case", case_id="test", agent_id="test")
    client = FakeClient(provider="fake", model="test", run_log=log)
    with pytest.raises(ValidationError):
        client.complete_json("PRIVATE_PROMPT", Item)
    (event,) = log.read_all()
    assert event.payload["validation_errors"] == [{"type": "int_parsing", "loc": ["value"]}]
    assert event.payload["usage"] is None
    assert event.cost.input_tokens is None
    assert event.cost.output_tokens is None
    assert event.cost.usd is None
    assert "PRIVATE" not in event.model_dump_json()


def test_unavailable_schema_cannot_hide_the_original_error():
    class BrokenSchema(Item):
        @classmethod
        def model_json_schema(cls, *args, **kwargs):
            raise RuntimeError("PRIVATE_SCHEMA_ERROR")

    with pytest.raises(ValidationError) as raised:
        BrokenSchema.model_validate({"value": "PRIVATE_VALUE"})
    assert safe_validation_errors(raised.value, BrokenSchema) == [
        {"type": "int_parsing", "loc": []}
    ]
