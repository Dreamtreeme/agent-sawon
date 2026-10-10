from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import BadRequestError
from pydantic import BaseModel, ValidationError

from brain.graph.matching import ScreenMatch
from models.base import TokenUsage, UsageUnavailableError
from models.budget import CallBudget
from models.factory import create_model_client
from models.openai_client import OpenAIClient
from models.pricing import PriceBook, PricingError
from sawon_runtime.storage import RunLog


class Answer(BaseModel):
    value: int


def setup(tmp_path, *, text='{"value":7}', status="completed", usage=True):
    sdk = Mock()
    sdk.responses.create.return_value = SimpleNamespace(
        output_text=text,
        status=status,
        incomplete_details=SimpleNamespace(reason="max_output_tokens")
        if status == "incomplete"
        else None,
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=30,
            input_tokens_details=SimpleNamespace(cached_tokens=40, cache_write_tokens=20),
            output_tokens_details=SimpleNamespace(reasoning_tokens=10),
        )
        if usage
        else None,
    )
    log = RunLog(tmp_path / "case", case_id="openai_test", agent_id="test")
    rate = PriceBook.load(Path("models/pricing.toml")).for_model("gpt-5.6-luna")
    client = OpenAIClient(sdk_client=sdk, model="gpt-5.6-luna", price=rate, run_log=log)
    return client, sdk, log


def openai_config(tmp_path: Path) -> Path:
    config = tmp_path / "openai-model.toml"
    config.write_text(
        'provider = "openai"\nmodel = "gpt-5.6-luna"\n'
        'pricing_path = "pricing.toml"\nmax_output_tokens = 8192\n',
        encoding="utf-8",
    )
    (tmp_path / "pricing.toml").write_text(
        Path("models/pricing.toml").read_text(), encoding="utf-8"
    )
    return config


def test_image_schema_and_actual_usage_share_measured_boundary(tmp_path):
    client, sdk, log = setup(tmp_path)
    from PIL import Image

    image = tmp_path / "screen.png"
    Image.new("RGB", (10, 10)).save(image)
    result = client.complete_json("Match this screen", Answer, images=[image])
    assert result.response.value == 7
    assert result.usage.response_tokens == 20 and result.usage.thinking_tokens == 10
    assert result.usd == Decimal("0.0000498")
    request = sdk.responses.create.call_args.kwargs
    assert request["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
    assert request["text"]["format"]["schema"]["required"] == ["value"]
    assert request["text"]["format"]["schema"]["additionalProperties"] is False
    assert request["reasoning"] == {"effort": "low"}
    assert request["max_output_tokens"] == 8192 and request["store"] is False
    assert "temperature" not in request
    assert sdk.responses.create.call_count == len(log.read_all()) == 1


@pytest.mark.parametrize("mode", ["invalid", "incomplete", "no_usage", "http400"])
def test_failed_responses_keep_cost_and_never_retry(tmp_path, mode):
    client, sdk, log = setup(
        tmp_path,
        text="not JSON" if mode == "invalid" else '{"value":7}',
        status="incomplete" if mode == "incomplete" else "completed",
        usage=mode != "no_usage",
    )
    expected = UsageUnavailableError if mode == "no_usage" else ValidationError
    if mode == "http400":
        sdk.responses.create.side_effect = BadRequestError(
            "sensitive body",
            response=httpx.Response(400, request=httpx.Request("POST", "https://example.test")),
            body=None,
        )
        expected = BadRequestError
    with pytest.raises(expected):
        client.complete_json("private prompt", Answer)
    (event,) = log.read_all()
    assert (event.cost.usd is None) == (mode in {"no_usage", "http400"})
    assert sdk.responses.create.call_count == 1
    assert "private prompt" not in event.model_dump_json()
    assert "sensitive body" not in event.model_dump_json()
    if mode == "incomplete":
        assert event.payload["output_truncated"]


def test_factory_uses_explicit_key_and_disables_sdk_retries(tmp_path, monkeypatch):
    _, _, log = setup(tmp_path)
    config = openai_config(tmp_path)
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=fake\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-must-not-be-used")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_model_client(config, env_path=env, run_log=log)
    env.write_text("OPENAI_API_KEY=test-only\n", encoding="utf-8")
    constructor = Mock()
    monkeypatch.setattr("openai.OpenAI", constructor)
    client = create_model_client(config, env_path=env, run_log=log)
    assert client.model == "gpt-5.6-luna"
    assert constructor.call_args.kwargs["max_retries"] == 0
    assert constructor.call_args.kwargs["api_key"] == "test-only"
    assert constructor.call_args.kwargs["base_url"] == "https://api.openai.com/v1"


def test_long_context_cache_write_pricing_and_reservation(tmp_path):
    client, _, _ = setup(tmp_path)
    usage = TokenUsage(
        input_tokens=300_000,
        response_tokens=100,
        cached_input_tokens=10_000,
        cache_write_tokens=20_000,
    )
    assert client._cost(usage) == Decimal("0.11858")
    # 272,000 input tokens still use the standard tier; the next token changes all rates.
    assert client._cost(usage.model_copy(update={"input_tokens": 272_000})) == Decimal("0.05372")
    assert client._cost(usage.model_copy(update={"input_tokens": 272_001})) == Decimal("0.1073804")
    budget = CallBudget.from_config(openai_config(tmp_path))
    assert budget("x" * 300_000, Answer) > client._cost(usage)
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=10, response_tokens=0, cached_input_tokens=8, cache_write_tokens=3)
    with pytest.raises(PricingError):
        client._price.model_copy(update={"cache_write_usd_per_million": None}).cost(usage)


def test_screen_matching_schema_is_converted_without_changing_local_contract(tmp_path):
    local_schema = deepcopy(ScreenMatch.model_json_schema())
    minimal = {
        "controls": [{"id": 1, "role": "edit", "confidence": 0.99}],
        "procedure_step": "fill",
        "operation": "stop",
    }
    expected = ScreenMatch.model_validate(minimal)
    client, sdk, log = setup(tmp_path, text=expected.model_dump_json())
    reply = client.complete_json("screen", ScreenMatch)
    assert reply.response == expected
    assert len(log.read_all()) == sdk.responses.create.call_count == 1
    schema = sdk.responses.create.call_args.kwargs["text"]["format"]["schema"]

    def assert_strict(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                assert_strict(child)
        elif isinstance(node, list):
            for child in node:
                assert_strict(child)

    assert_strict(schema)
    assert "steps" in schema["required"] and "steps" not in local_schema["required"]
    assert ScreenMatch.model_json_schema() == local_schema
    assert ScreenMatch.model_validate(minimal) == expected
    assert expected.steps == [] and expected.grid is None and expected.controls[0].enabled
