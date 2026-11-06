import base64
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from pydantic import BaseModel

from brain.graph.screen_decision import ScreenMatch
from brain.visual.scene import SemanticDetection
from models.anthropic_client import AnthropicClient, anthropic_provider_schema
from models.base import TokenUsage
from models.budget import CallBudget
from models.factory import create_model_client
from models.pricing import PriceBook
from models.schema import NullableWireCodec
from sawon_runtime.storage import RunLog


class Answer(BaseModel):
    value: int


def setup(tmp_path: Path):
    sdk = Mock()
    sdk.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"value":7}')],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=30,
            cache_creation_input_tokens=20,
            cache_read_input_tokens=40,
        ),
    )
    log = RunLog(tmp_path / "case", case_id="anthropic_test", agent_id="test")
    rate = PriceBook.load(Path("models/pricing.toml")).for_model("claude-haiku-5-5")
    client = AnthropicClient(
        sdk_client=sdk,
        model="claude-haiku-5-5",
        price=rate,
        run_log=log,
    )
    return client, sdk, log


def test_structured_vision_request_and_usage_share_one_measured_call(tmp_path):
    client, sdk, log = setup(tmp_path)
    image = tmp_path / "screen.png"
    Image.new("RGB", (10, 10)).save(image)
    budget = CallBudget.from_config(Path(__file__).parents[1] / "config/models.toml")
    assert budget is not None
    reserved = budget("Match this screen", Answer)

    result = client.complete_json("Match this screen", Answer, images=[image])

    assert result.response == Answer(value=7)
    assert result.usage == TokenUsage(
        input_tokens=160,
        response_tokens=30,
        cached_input_tokens=40,
        cache_write_tokens=20,
    )
    assert result.usd == Decimal("0.0000279")
    assert reserved > result.usd
    request = sdk.messages.create.call_args.kwargs
    assert request["model"] == "claude-haiku-5-5"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"]["effort"] == "low"
    assert request["max_tokens"] == budget.max_output_tokens == 8192
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["messages"][0]["content"][0]["source"]["media_type"] == "image/png"
    assert base64.b64decode(request["messages"][0]["content"][0]["source"]["data"]) == (
        image.read_bytes()
    )
    assert sdk.messages.create.call_count == len(log.read_all()) == 1


@pytest.mark.parametrize("contract", [ScreenMatch, SemanticDetection])
def test_screen_schema_removes_anthropic_optional_grammar_branches(contract):
    schema = anthropic_provider_schema(contract)
    optional = []
    unions = []

    def inspect(node):
        if isinstance(node, dict):
            properties = node.get("properties")
            if node.get("type") == "object" and isinstance(properties, dict):
                required = set(node.get("required", []))
                optional.extend(key for key in properties if key not in required)
            if "anyOf" in node or "oneOf" in node or isinstance(node.get("type"), list):
                unions.append(node)
            for child in node.values():
                inspect(child)
        elif isinstance(node, list):
            for child in node:
                inspect(child)

    inspect(schema)
    assert optional == []
    assert unions == []  # Local complexity check; actual provider acceptance is tested separately.
    if contract is ScreenMatch:
        assert schema["properties"]["steps"]["items"]["type"] == "object"
        assert "execute now" in schema["properties"]["steps"]["description"]
        assert "task decision" in schema["description"]
        from brain.graph.screen_mapping import ScreenMappingWire

        assert schema != anthropic_provider_schema(ScreenMappingWire)


def test_long_prompt_tier_changes_all_haiku_rates(tmp_path):
    client, _, _ = setup(tmp_path)
    usage = TokenUsage(
        input_tokens=100_001,
        response_tokens=100,
        cached_input_tokens=10_000,
        cache_write_tokens=20_000,
    )
    assert client._cost(usage) == Decimal("0.0482505")
    assert client._cost(usage.model_copy(update={"input_tokens": 100_000})) == Decimal("0.00965")


def test_factory_reads_only_explicit_claude_key_and_disables_retries(tmp_path, monkeypatch):
    _, _, log = setup(tmp_path)
    config = tmp_path / "models.toml"
    config.write_text(
        'provider = "anthropic"\nmodel = "claude-haiku-5-5"\n'
        'pricing_path = "pricing.toml"\nthinking_level = "low"\n',
        encoding="utf-8",
    )
    (tmp_path / "pricing.toml").write_text(
        Path("models/pricing.toml").read_text(), encoding="utf-8"
    )
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=wrong-provider\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_API_KEY", "ambient-must-not-be-used")
    with pytest.raises(ValueError, match="CLAUDE_API_KEY"):
        create_model_client(config, env_path=env, run_log=log)

    env.write_text("CLAUDE_API_KEY=test-only\n", encoding="utf-8")
    constructor = Mock()
    monkeypatch.setattr("anthropic.Anthropic", constructor)
    client = create_model_client(config, env_path=env, run_log=log)
    assert client.model == "claude-haiku-5-5"
    assert constructor.call_args.kwargs == {
        "api_key": "test-only",
        "max_retries": 0,
        "timeout": 60.0,
        "base_url": "https://api.anthropic.com",
    }


def test_screen_wire_restores_inline_target_nulls_without_losing_zero_or_source(tmp_path):
    client, sdk, log = setup(tmp_path)
    raw = {
        "screen_kind": "editor",
        "procedure_step": "task-action",
        "fields": [
            {"name": "Reference", "source": "hbl_no", "role": "edit", "bbox": [0, 0, 100, 30]}
        ],
        "taught_step_id": "fill-details",
        "method_id": None,
        "grid": "Reference",
        "row": None,
        "save_result": None,
        "steps": [
            {
                "target": "Reference",
                "action": "fill",
                "intent": "fill",
                "source": "hbl_no",
                "point": [0, 0],
            },
            {
                "target": "Reference",
                "action": "key",
                "intent": "navigate",
                "keys": "tab",
                "point": [5, 5],
            },
            {
                "target": "Reference",
                "action": "click",
                "intent": "navigate",
                "source": None,
                "point": [5, 5],
            },
            {
                "target": "Reference",
                "action": "scroll",
                "intent": "navigate",
                "direction": "up",
                "point": [150, 150],
            },
        ],
    }
    original = deepcopy(raw)
    original_schema = ScreenMatch.model_json_schema()
    codec = NullableWireCodec(
        original_schema, ScreenMatch.anthropic_nulls, ScreenMatch.anthropic_unions
    )
    encoded = codec.convert(raw, encode=True)
    assert encoded["steps"][0]["target"] == encoded["grid"] == "Reference"
    assert encoded["steps"][0]["point"] == [0, 0]
    assert encoded["steps"][1]["point"] == [5, 5]
    assert encoded["steps"][2]["source"] == ""
    assert encoded["steps"][3]["direction"] == "up"
    assert encoded["steps"][3]["source"] == encoded["steps"][3]["keys"] == ""
    assert encoded["steps"][2]["keys"] == encoded["steps"][2]["direction"] == ""
    sdk.messages.create.return_value.content[0].text = json.dumps(encoded)
    result = client.complete_json("current screen", ScreenMatch)
    assert result.response == ScreenMatch.model_validate(original)
    assert raw == original
    assert ScreenMatch.model_json_schema() == original_schema
    payload = log.read_all()[-1].payload
    assert payload["wire_schema_codec"] == "nullable-sentinels-flat-actions-v2"
    assert payload["wire_schema_union_count"] == 0
    assert len(payload["wire_schema_sha256"]) == 64
    sent = sdk.messages.create.call_args.kwargs["output_config"]["format"]["schema"]
    assert payload["wire_schema_utf8_bytes"] == len(
        json.dumps(sent, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    )
    action = sent["properties"]["steps"]["items"]["properties"]
    assert "pageup" not in action["keys"]["enum"]
    assert action["direction"]["enum"] == ["up", "down", ""]
    assert "x-anthropic-null" not in json.dumps(sent)
    # Invalid nonempty payloads still fail the original domain validation with usage retained.
    for index, key, value in [
        (1, "keys", "not-a-key"),
        (1, "keys", "pageup"),
        (2, "keys", "pageup"),
        (2, "direction", "up"),
        (3, "keys", "tab"),
        (3, "source", "hbl_no"),
    ]:
        invalid = deepcopy(encoded)
        invalid["steps"][index][key] = value
        sdk.messages.create.return_value.content[0].text = json.dumps(invalid)
        with pytest.raises(ValueError):
            client.complete_json(f"invalid step {index}: {key}={value}", ScreenMatch)
        assert log.read_all()[-1].cost.usd is not None, (index, key, value)


def test_wire_metadata_survives_api_failure_and_does_not_leak_to_next_call(tmp_path):
    import httpx
    from anthropic import BadRequestError

    client, sdk, log = setup(tmp_path)
    sdk.messages.create.side_effect = BadRequestError(
        "grammar too large",
        response=httpx.Response(400, request=httpx.Request("POST", "https://example.test")),
        body={"type": "invalid_request_error"},
    )
    with pytest.raises(BadRequestError):
        client.complete_json("screen", ScreenMatch)
    failed = log.read_all()[-1]
    assert failed.payload["wire_schema_codec"] == "nullable-sentinels-flat-actions-v2"
    assert failed.payload["http_status"] == 400
    assert failed.cost.usd is None
    assert sdk.messages.create.call_count == 1
    sdk.messages.create.side_effect = None
    client.complete_json("number", Answer)
    succeeded = log.read_all()[-1].payload
    assert succeeded["wire_schema_codec"] == "none"
    assert succeeded["wire_schema_sha256"] != failed.payload["wire_schema_sha256"]


def test_api_failure_logs_status_without_request_or_response_secrets(tmp_path):
    import httpx
    from anthropic import APIStatusError

    client, sdk, log = setup(tmp_path)
    error = APIStatusError(
        "PRIVATE_PROMPT SECRET_KEY",
        response=httpx.Response(
            503,
            text="PRIVATE_RESPONSE_BODY",
            request=httpx.Request(
                "POST",
                "https://example.test/?key=SECRET_KEY",
                headers={"Authorization": "SECRET_BEARER"},
            ),
        ),
        body={"message": "PRIVATE_PROMPT SECRET_KEY"},
    )
    sdk.messages.create.side_effect = error
    with pytest.raises(APIStatusError) as raised:
        client.complete_json("PRIVATE_PROMPT", Answer)
    assert raised.value is error
    (event,) = log.read_all()
    assert event.payload["http_status"] == 503
    assert event.payload["error_type"] == "APIStatusError"
    assert event.cost.usd is None
    assert event.cost.input_tokens is None and event.cost.output_tokens is None
    for private in ("PRIVATE_PROMPT", "SECRET_KEY", "SECRET_BEARER", "PRIVATE_RESPONSE_BODY"):
        assert private not in event.model_dump_json()
    sdk.messages.create.assert_called_once()


def test_unannotated_nullable_contract_remains_unchanged():
    class Document(BaseModel):
        note: str | None = None

    raw = Document.model_json_schema()
    codec = NullableWireCodec(raw, {})
    assert codec.schema == raw
    assert codec.convert({"note": ""}) == {"note": ""}
    assert codec.convert({"note": None}) == {"note": None}
    assert "anyOf" in anthropic_provider_schema(Document)["properties"]["note"]
