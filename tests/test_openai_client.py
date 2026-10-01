from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import BadRequestError
from pydantic import BaseModel, ValidationError

from brain.graph.matching import ScreenMatch
from brain.runtime.storage import RunLog
from models.base import TokenUsage, UsageUnavailableError
from models.budget import CallBudget
from models.factory import create_model_client
from models.openai_client import OpenAIClient
from models.pricing import PriceBook, PricingError


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
    rate = PriceBook.load(Path("config/pricing.openai.toml")).for_model("gpt-5.6-luna")
    client = OpenAIClient(sdk_client=sdk, model="gpt-5.6-luna", price=rate, run_log=log)
    return client, sdk, log


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
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=fake\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-must-not-be-used")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_model_client(Path("config/models.luna.toml"), env_path=env, run_log=log)
    env.write_text("OPENAI_API_KEY=test-only\n", encoding="utf-8")
    constructor = Mock()
    monkeypatch.setattr("openai.OpenAI", constructor)
    client = create_model_client(Path("config/models.luna.toml"), env_path=env, run_log=log)
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
    budget = CallBudget.from_config(Path("config/models.luna.toml"))
    assert budget("x" * 300_000, Answer) > client._cost(usage)
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=10, response_tokens=0, cached_input_tokens=8, cache_write_tokens=3)
    with pytest.raises(PricingError):
        client._price.model_copy(update={"cache_write_usd_per_million": None}).cost(usage)


def test_screen_matching_schema_is_converted_without_changing_local_contract(tmp_path):
    client, sdk, _ = setup(tmp_path)
    with pytest.raises(ValidationError):
        client.complete_json("screen", ScreenMatch)
    schema = sdk.responses.create.call_args.kwargs["text"]["format"]["schema"]
    for obj in [schema, *schema["$defs"].values()]:
        if obj.get("type") == "object":
            assert obj["additionalProperties"] is False
            assert set(obj["required"]) == set(obj["properties"])
