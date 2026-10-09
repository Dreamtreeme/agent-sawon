from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from models import (
    GeminiClient,
    ReplayClient,
    ReplayEntry,
    ReplayExhaustedError,
    ReplayMismatchError,
    TokenUsage,
    UsageUnavailableError,
    create_model_client,
)
from models.base import request_fingerprint
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


def sdk_response(text: str = '{"value": 7}') -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100,
            candidates_token_count=20,
            thoughts_token_count=10,
            cached_content_token_count=40,
        ),
    )


def fake_gemini(run_log: RunLog, rate: PriceRate, response=None) -> tuple[GeminiClient, Mock]:
    sdk = Mock()
    sdk.models.generate_content.return_value = response or sdk_response()
    client = GeminiClient(sdk_client=sdk, model="test-model", price=rate, run_log=run_log)
    return client, sdk


def replay_entry(prompt: str = "test", response: str = '{"value": 7}') -> ReplayEntry:
    return ReplayEntry(
        request_sha256=request_fingerprint(prompt, Answer, None),
        response_json=response,
        recorded_usage=TokenUsage(input_tokens=100, response_tokens=20, thinking_tokens=10),
        recorded_usd=Decimal("0.000105"),
    )


def test_gemini_accounts_for_cached_input_and_thinking(run_log: RunLog, rate: PriceRate):
    client, sdk = fake_gemini(run_log, rate)
    result = client.complete_json("test", Answer)
    assert isinstance(result.response, Answer)
    assert result.response.value == 7
    assert result.usage.output_tokens == 30
    assert result.usd == Decimal("0.0000942")
    assert result.latency_ms >= 0
    config = sdk.models.generate_content.call_args.kwargs["config"]
    assert config.response_json_schema == Answer.model_json_schema()
    assert config.response_mime_type == "application/json"
    assert config.candidate_count == 1
    assert config.http_options.retry_options.attempts == 1
    assert config.automatic_function_calling.disable is True
    (event,) = run_log.read_all()
    assert event.kind == "model_call"
    assert event.payload["status"] == "success"
    assert event.payload["usage"]["thinking_tokens"] == 10
    assert event.cost.output_tokens == 30
    assert event.cost.usd == pytest.approx(0.0000942)


def test_gemini_validation_failure_keeps_billable_usage(run_log: RunLog, rate: PriceRate):
    client, _ = fake_gemini(run_log, rate, sdk_response('{"value": "invalid"}'))
    with pytest.raises(ValidationError):
        client.complete_json("private prompt", Answer)
    (event,) = run_log.read_all()
    assert event.payload["status"] == "error"
    assert event.payload["error_type"] == "ValidationError"
    assert event.cost.input_tokens == 100
    assert event.cost.usd == pytest.approx(0.0000942)
    assert "private prompt" not in event.model_dump_json()
    assert "invalid" not in event.model_dump_json()


def test_sdk_failure_logs_unknown_cost_without_exception_secrets(run_log: RunLog, rate: PriceRate):
    client, sdk = fake_gemini(run_log, rate)
    sdk.models.generate_content.side_effect = TimeoutError("API_KEY=secret prompt contents")
    with pytest.raises(TimeoutError):
        client.complete_json("private prompt", Answer)
    (event,) = run_log.read_all()
    assert event.payload["error_type"] == "TimeoutError"
    assert event.cost.input_tokens is None
    assert event.cost.output_tokens is None
    assert event.cost.usd is None
    assert "secret" not in event.model_dump_json()
    sdk.models.generate_content.assert_called_once()


@pytest.mark.parametrize(
    "code,category,status",
    [
        (503, "server_error", "UNAVAILABLE"),
        (500, "server_error", "INTERNAL"),
        (429, "rate_limited", "RESOURCE_EXHAUSTED"),
        (403, "authentication_or_permission", "PERMISSION_DENIED"),
        (401, "authentication_or_permission", "UNAUTHENTICATED"),
        (404, "client_error", "NOT_FOUND"),
        (400, "client_error", "INVALID_ARGUMENT"),
        (504, "timeout", "DEADLINE_EXCEEDED"),
    ],
)
def test_sdk_api_failure_logs_only_allowlisted_metadata(
    run_log: RunLog,
    rate: PriceRate,
    code,
    category,
    status,
):
    client, sdk = fake_gemini(run_log, rate)
    response = SimpleNamespace(
        text="PRIVATE_RESPONSE_BODY",
        request=SimpleNamespace(url="https://host/?key=SECRET_KEY"),
    )
    exception_type = errors.ServerError if code >= 500 else errors.ClientError
    error = exception_type(
        code,
        {
            "error": {"status": status, "message": "PRIVATE_PROMPT SECRET_KEY"},
            "details": {"headers": {"Authorization": "SECRET_BEARER"}},
        },
        response=response,
    )
    sdk.models.generate_content.side_effect = error
    with pytest.raises(errors.APIError) as raised:
        client.complete_json("PRIVATE_PROMPT", Answer)
    assert raised.value is error
    (event,) = run_log.read_all()
    assert event.payload["http_status"] == code
    assert event.payload["error_type"] == exception_type.__name__
    assert event.payload["error_category"] == category
    assert event.payload["provider_status"] == status
    assert event.cost.usd is None
    assert event.cost.input_tokens is None and event.cost.output_tokens is None
    encoded = event.model_dump_json()
    for private in ("PRIVATE_PROMPT", "SECRET_KEY", "SECRET_BEARER", "PRIVATE_RESPONSE_BODY"):
        assert private not in encoded
    sdk.models.generate_content.assert_called_once()


@pytest.mark.parametrize(
    "code,status",
    [
        ("503 SECRET_KEY", "UNAVAILABLE SECRET_KEY"),
        (True, {"private": "SECRET_KEY"}),
        (999, "https://host/?key=SECRET_KEY"),
        (503.0, ["UNAVAILABLE", "SECRET_KEY"]),
    ],
)
def test_sdk_metadata_does_not_coerce_or_publish_untrusted_values(
    run_log: RunLog,
    rate: PriceRate,
    code,
    status,
):
    client, sdk = fake_gemini(run_log, rate)
    error = errors.APIError(503, {"error": {"status": "UNAVAILABLE", "message": "SECRET_KEY"}})
    error.code, error.status = code, status
    sdk.models.generate_content.side_effect = error
    with pytest.raises(errors.APIError):
        client.complete_json("private", Answer)
    (event,) = run_log.read_all()
    assert "http_status" not in event.payload
    assert "error_category" not in event.payload
    assert "provider_status" not in event.payload
    assert "SECRET_KEY" not in event.model_dump_json()
    assert event.cost.usd is None
    sdk.models.generate_content.assert_called_once()


def test_missing_usage_fails_instead_of_claiming_zero(run_log: RunLog, rate: PriceRate):
    client, _ = fake_gemini(
        run_log, rate, SimpleNamespace(text='{"value": 7}', usage_metadata=None)
    )
    with pytest.raises(UsageUnavailableError):
        client.complete_json("test", Answer)
    (event,) = run_log.read_all()
    assert event.cost.usd is None
    assert event.payload["status"] == "error"


def test_image_is_passed_as_inline_bytes(run_log: RunLog, rate: PriceRate, tmp_path: Path):
    image = tmp_path / "screen.png"
    image.write_bytes(b"synthetic-image-bytes")
    client, sdk = fake_gemini(run_log, rate)
    client.complete_json("test", Answer, images=[image])
    content = sdk.models.generate_content.call_args.kwargs["contents"][0]
    assert content.parts[1].inline_data.mime_type == "image/png"
    assert content.parts[1].inline_data.data == image.read_bytes()


def test_missing_image_is_logged_without_calling_sdk(
    run_log: RunLog, rate: PriceRate, tmp_path: Path
):
    client, sdk = fake_gemini(run_log, rate)
    with pytest.raises(FileNotFoundError):
        client.complete_json("test", Answer, images=[tmp_path / "missing.png"])
    sdk.models.generate_content.assert_not_called()
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
        PriceRate(input_usd_per_million="NaN", output_usd_per_million=1)


def test_pricing_failure_preserves_known_tokens(run_log: RunLog):
    rate = PriceRate(input_usd_per_million=1, output_usd_per_million=1)
    client, _ = fake_gemini(run_log, rate)
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
    monkeypatch.setenv("GEMINI_API_KEY", "ambient-key-must-not-be-used")
    with pytest.raises(ValueError, match="explicitly specified"):
        create_model_client(Path(__file__).parents[1] / "config/models.toml", run_log=run_log)


def test_factory_uses_only_explicit_file_and_disables_retries(
    run_log: RunLog, tmp_path: Path, monkeypatch
):
    config = tmp_path / "gemini.toml"
    config.write_text(
        'provider = "gemini"\nmodel = "test-model"\npricing_path = "pricing.toml"\n',
        encoding="utf-8",
    )
    (tmp_path / "pricing.toml").write_text(
        'checked_on = 2026-10-01\nsource = "test fixture"\n'
        'basis = "standard_paid_text_image"\n[models.test-model]\n'
        'input_usd_per_million = "0.30"\noutput_usd_per_million = "2.50"\n',
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=synthetic-test-key\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "ambient-key-must-not-be-used")
    constructor = Mock()
    monkeypatch.setattr("google.genai.Client", constructor)
    create_model_client(config, run_log=run_log, env_path=env)
    kwargs = constructor.call_args.kwargs
    assert kwargs["api_key"] == "synthetic-test-key"
    assert kwargs["vertexai"] is False
    assert kwargs["http_options"].retry_options.attempts == 1
    assert kwargs["http_options"].timeout == 60_000


def test_log_failure_is_not_hidden(rate: PriceRate):
    sink = Mock()
    sink.append.side_effect = OSError("log disk unavailable")
    client, _ = fake_gemini(sink, rate)
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


def test_replay_config_loads_explicit_fixture_without_credentials(tmp_path, run_log):
    fixture = tmp_path / "responses.jsonl"
    fixture.write_text(replay_entry().model_dump_json() + "\n", encoding="utf-8")
    config = tmp_path / "models.toml"
    config.write_text('provider = "replay"\nreplay_path = "responses.jsonl"\n', encoding="utf-8")
    client = create_model_client(config, run_log=run_log)
    result = client.complete_json("test", Answer)
    assert result.response == Answer(value=7)
    assert result.usd == 0


def test_gemini_truncated_json_preserves_usage_and_truncation_diagnostic(run_log, rate):
    response = sdk_response('{"value":')
    response.candidates = [SimpleNamespace(finish_reason=types.FinishReason.MAX_TOKENS)]
    client, _ = fake_gemini(run_log, rate, response)
    with pytest.raises(ValidationError):
        client.complete_json("test", Answer)
    event = run_log.read_all()[-1]
    assert event.payload["output_truncated"] is True
    assert event.payload["usage"]["response_tokens"] == 20
    assert event.cost.usd is not None


def test_gemini_configures_thinking_without_raising_output_limit(run_log, rate):
    sdk = Mock()
    sdk.models.generate_content.return_value = sdk_response()
    client = GeminiClient(
        sdk_client=sdk,
        model="test-model",
        price=rate,
        run_log=run_log,
        max_output_tokens=8192,
        thinking_level="low",
    )
    client.complete_json("test", Answer)
    config = sdk.models.generate_content.call_args.kwargs["config"]
    assert config.max_output_tokens == 8192
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
