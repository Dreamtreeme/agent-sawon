from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from pydantic import BaseModel

from models.anthropic_client import AnthropicClient
from models.base import TokenUsage
from models.factory import create_model_client
from models.pricing import PriceBook
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

    result = client.complete_json("Match this screen", Answer, images=[image])

    assert result.response == Answer(value=7)
    assert result.usage == TokenUsage(
        input_tokens=160,
        response_tokens=30,
        cached_input_tokens=40,
        cache_write_tokens=20,
    )
    assert result.usd == Decimal("0.0000279")
    request = sdk.messages.create.call_args.kwargs
    assert request["model"] == "claude-haiku-5-5"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"]["effort"] == "low"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["messages"][0]["content"][0]["source"]["media_type"] == "image/png"
    assert sdk.messages.create.call_count == len(log.read_all()) == 1


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
