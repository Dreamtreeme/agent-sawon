from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, ValidationError

from models import (
    ReplayClient,
    ReplayEntry,
    ReplayExhaustedError,
    ReplayMismatchError,
    TokenUsage,
    UsageUnavailableError,
    create_model_client,
)
from models.base import MeasuredModelClient, ProviderReply, request_fingerprint
from models.pricing import PriceBook, PriceRate, PricingError
from sawon_runtime.storage import RunLog


class Answer(BaseModel):
    value: int


@pytest.fixture
def run_log(tmp_path: Path) -> RunLog:
    return RunLog(tmp_path / "case", case_id="model_test", agent_id="test")


@pytest.fixture
def rate() -> PriceRate:
    return PriceRate(
        input_usd_per_million="0.30",
        output_usd_per_million="2.50",
        cached_input_usd_per_million="0.03",
    )


def provider_reply(text: str = '{"value": 7}') -> ProviderReply:
    return ProviderReply(
        text=text,
        usage=TokenUsage(
            input_tokens=100,
            response_tokens=20,
            thinking_tokens=10,
            cached_input_tokens=40,
        ),
    )


class FakeMeasuredClient(MeasuredModelClient):
    def __init__(self, run_log, rate, reply=None):
        super().__init__(provider="synthetic", model="test-model", run_log=run_log)
        self.generate = Mock(return_value=reply or provider_reply())
        self.price = rate

    def _generate(self, prompt, schema, *, images=None):
        return self.generate(prompt, schema, images=images)

    def _cost(self, usage):
        return self.price.cost(usage)


def replay_entry(prompt: str = "test", response: str = '{"value": 7}') -> ReplayEntry:
    return ReplayEntry(
        request_sha256=request_fingerprint(prompt, Answer, None),
        response_json=response,
        recorded_usage=TokenUsage(input_tokens=100, response_tokens=20, thinking_tokens=10),
        recorded_usd=Decimal("0.000105"),
    )


def test_measured_call_accounts_for_cached_input_and_thinking(run_log: RunLog, rate: PriceRate):
    client = FakeMeasuredClient(run_log, rate)
    result = client.complete_json("test", Answer)
    assert isinstance(result.response, Answer)
    assert result.response.value == 7
    assert result.usage.output_tokens == 30
    assert result.usd == Decimal("0.0000942")
    assert result.latency_ms >= 0
    client.generate.assert_called_once_with("test", Answer, images=None)
    (event,) = run_log.read_all()
    assert event.kind == "model_call"
    assert event.payload["status"] == "success"
    assert event.payload["usage"]["thinking_tokens"] == 10
    assert event.cost.output_tokens == 30
    assert event.cost.usd == pytest.approx(0.0000942)


def test_validation_failure_keeps_billable_usage(run_log: RunLog, rate: PriceRate):
    client = FakeMeasuredClient(run_log, rate, provider_reply('{"value": "invalid"}'))
    with pytest.raises(ValidationError):
        client.complete_json("private prompt", Answer)
    (event,) = run_log.read_all()
    assert event.payload["status"] == "error"
    assert event.payload["error_type"] == "ValidationError"
    assert event.cost.input_tokens == 100
    assert event.cost.usd == pytest.approx(0.0000942)
    assert "private prompt" not in event.model_dump_json()
    assert "invalid" not in event.model_dump_json()


def test_provider_failure_logs_unknown_cost_without_exception_secrets(
    run_log: RunLog, rate: PriceRate
):
    client = FakeMeasuredClient(run_log, rate)
    client.generate.side_effect = TimeoutError("API_KEY=secret prompt contents")
    with pytest.raises(TimeoutError):
        client.complete_json("private prompt", Answer)
    (event,) = run_log.read_all()
    assert event.payload["error_type"] == "TimeoutError"
    assert event.cost.input_tokens is None
    assert event.cost.output_tokens is None
    assert event.cost.usd is None
    assert "secret" not in event.model_dump_json()
    client.generate.assert_called_once()


def test_missing_usage_fails_instead_of_claiming_zero(run_log: RunLog, rate: PriceRate):
    client = FakeMeasuredClient(run_log, rate, ProviderReply(text='{"value": 7}', usage=None))
    with pytest.raises(UsageUnavailableError):
        client.complete_json("test", Answer)
    (event,) = run_log.read_all()
    assert event.cost.usd is None
    assert event.payload["status"] == "error"


def test_missing_image_is_logged_without_calling_provider(
    run_log: RunLog, rate: PriceRate, tmp_path: Path
):
    client = FakeMeasuredClient(run_log, rate)
    with pytest.raises(FileNotFoundError):
        client.complete_json("test", Answer, images=[tmp_path / "missing.png"])
    client.generate.assert_not_called()
    assert run_log.read_all()[0].payload["error_type"] == "FileNotFoundError"


def test_replay_determinism_and_zero_actual_usage(run_log: RunLog):
    client = ReplayClient([replay_entry()], run_log=run_log)
    result = client.complete_json("test", Answer)
    assert result.response.value == 7
    assert result.usd == 0
    assert result.usage.input_tokens == result.usage.output_tokens == 0
    assert result.recorded_usage.input_tokens == 100
    assert result.recorded_usd == Decimal("0.000105")
    (event,) = run_log.read_all()
    assert event.cost.usd == 0
    assert event.payload["recorded_usage"]["thinking_tokens"] == 10
    assert event.payload["usage_source"] == "replay_actual_zero"
    assert client.remaining == 0


def test_replay_mismatch_does_not_consume_entry(run_log: RunLog):
    client = ReplayClient([replay_entry()], run_log=run_log)
    with pytest.raises(ReplayMismatchError):
        client.complete_json("changed prompt", Answer)
    assert client.remaining == 1
    assert client.complete_json("test", Answer).response.value == 7
    assert [event.payload["status"] for event in run_log.read_all()] == ["error", "success"]


def test_replay_schema_and_image_are_bound(run_log: RunLog, tmp_path: Path):
    class OtherAnswer(BaseModel):
        different: str

    client = ReplayClient([replay_entry()], run_log=run_log)
    with pytest.raises(ReplayMismatchError):
        client.complete_json("test", OtherAnswer)
    image = tmp_path / "image.png"
    image.write_bytes(b"first")
    entry = replay_entry().model_copy(
        update={"request_sha256": request_fingerprint("test", Answer, [image])}
    )
    client = ReplayClient([entry], run_log=run_log)
    image.write_bytes(b"second")
    with pytest.raises(ReplayMismatchError):
        client.complete_json("test", Answer, images=[image])


def test_replay_exhaustion_and_validation_are_logged(run_log: RunLog):
    client = ReplayClient([replay_entry(response="not JSON")], run_log=run_log)
    with pytest.raises(ValidationError):
        client.complete_json("test", Answer)
    with pytest.raises(ReplayExhaustedError):
        client.complete_json("test", Answer)
    events = run_log.read_all()
    assert [event.payload["error_type"] for event in events] == [
        "ValidationError",
        "ReplayExhaustedError",
    ]
    assert all(event.cost.usd == 0 for event in events)


def test_unknown_prices_and_invalid_usage_fail_explicitly():
    book = PriceBook.load(Path(__file__).parents[1] / "models" / "pricing.toml")
    with pytest.raises(PricingError, match="No configured price"):
        book.for_model("unpriced-model")
    rate = PriceRate(input_usd_per_million=1, output_usd_per_million=1)
    with pytest.raises(PricingError, match="Cached input"):
        rate.cost(TokenUsage(input_tokens=5, response_tokens=1, cached_input_tokens=2))
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=1, response_tokens=1, cached_input_tokens=2)
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=10, response_tokens=0, cached_input_tokens=8, cache_write_tokens=3)
    with pytest.raises(PricingError, match="Cache write"):
        rate.cost(TokenUsage(input_tokens=10, response_tokens=0, cache_write_tokens=3))
    with pytest.raises(ValidationError):
        PriceRate(input_usd_per_million="NaN", output_usd_per_million=1)


def test_pricing_failure_preserves_known_tokens(run_log: RunLog):
    rate = PriceRate(input_usd_per_million=1, output_usd_per_million=1)
    client = FakeMeasuredClient(run_log, rate)
    with pytest.raises(PricingError):
        client.complete_json("test", Answer)
    (event,) = run_log.read_all()
    assert event.cost.input_tokens == 100
    assert event.cost.output_tokens == 30
    assert event.cost.usd is None


def test_factory_replay_does_not_read_env(run_log: RunLog, tmp_path: Path, monkeypatch):
    fixture = tmp_path / "responses.jsonl"
    fixture.write_text(replay_entry().model_dump_json() + "\n", encoding="utf-8")
    config = tmp_path / "models.toml"
    config.write_text('provider = "replay"\nreplay_path = "responses.jsonl"\n', encoding="utf-8")
    read_env = Mock(side_effect=AssertionError("Replay must not read credentials"))
    monkeypatch.setattr("models.factory.dotenv_values", read_env)
    client = create_model_client(config, run_log=run_log)
    assert client.complete_json("test", Answer).response.value == 7
    read_env.assert_not_called()
    config.write_text('provider = "replay"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="explicit replay_path"):
        create_model_client(config, run_log=run_log)
    read_env.assert_not_called()


def test_factory_requires_explicit_env_even_with_ambient_key(run_log: RunLog, monkeypatch):
    monkeypatch.setenv("CLAUDE_API_KEY", "ambient-key-must-not-be-used")
    with pytest.raises(ValueError, match="explicitly specified"):
        create_model_client(Path(__file__).parents[1] / "config/models.toml", run_log=run_log)


def test_log_failure_is_not_hidden(rate: PriceRate):
    sink = Mock()
    sink.append.side_effect = OSError("log disk unavailable")
    client = FakeMeasuredClient(sink, rate)
    with pytest.raises(OSError, match="log disk"):
        client.complete_json("test", Answer)


def test_jsonl_fixture_rejects_malformed_entries(run_log: RunLog, tmp_path: Path):
    fixture = tmp_path / "responses.jsonl"
    fixture.write_text(json.dumps({"response_json": "{}"}), encoding="utf-8")
    with pytest.raises(ValidationError):
        ReplayClient.from_jsonl(fixture, run_log=run_log)


def test_cost_only_replay_metadata_is_preserved(run_log: RunLog):
    entry = replay_entry().model_copy(update={"recorded_usage": None})
    result = ReplayClient([entry], run_log=run_log).complete_json("test", Answer)
    assert result.recorded_usd == Decimal("0.000105")
    assert run_log.read_all()[0].payload["recorded_usd"] == "0.000105"


def test_truncated_json_preserves_usage_and_truncation_diagnostic(run_log, rate):
    reply = ProviderReply(text='{"value":', usage=provider_reply().usage, output_truncated=True)
    client = FakeMeasuredClient(run_log, rate, reply)
    with pytest.raises(ValidationError):
        client.complete_json("test", Answer)
    event = run_log.read_all()[-1]
    assert event.payload["output_truncated"] is True
    assert event.payload["usage"]["response_tokens"] == 20
    assert event.cost.usd is not None


def test_model_call_context_records_failure_and_does_not_leak(run_log, rate):
    from models.base import model_call_context

    client = FakeMeasuredClient(run_log, rate, provider_reply('{"value": "invalid"}'))
    with (
        pytest.raises(ValidationError),
        model_call_context(purpose="input_review", decision_id="decision-1", frame="frame.png"),
    ):
        client.complete_json("private prompt", Answer)
    good = FakeMeasuredClient(run_log, rate)
    good.complete_json("test", Answer)
    failed, succeeded = run_log.read_all()
    assert failed.payload["context"]["purpose"] == "input_review"
    assert failed.payload["status"] == "error"
    assert succeeded.payload["context"] == {}
    assert failed.payload["prompt_utf8_bytes"] == len(b"private prompt")
    assert "private prompt" not in json.dumps(failed.payload)
