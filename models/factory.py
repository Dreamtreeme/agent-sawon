"""Config-driven clients; secrets are loaded only from an explicit .env file."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

from models.base import EventSink, ModelClient
from models.pricing import PriceBook
from models.replay import ReplayClient
from models.settings import ModelSettings


def create_model_client(
    config_path: Path,
    *,
    run_log: EventSink,
    env_path: Path | None = None,
    provider: Literal["anthropic", "gemini", "replay", "openai"] | None = None,
) -> ModelClient:
    """Replay needs no .env; paid providers never use ambient credentials."""
    with config_path.open("rb") as handle:
        settings = ModelSettings.model_validate(tomllib.load(handle))
    selected = provider if provider is not None else settings.provider
    if selected not in {"anthropic", "gemini", "replay", "openai"}:
        raise ValueError("Unknown model provider")
    if selected == "replay":
        if not settings.replay_path:
            raise ValueError("Replay requires an explicit replay_path")
        return ReplayClient.from_jsonl(config_path.parent / settings.replay_path, run_log=run_log)
    price = PriceBook.load(config_path.parent / settings.pricing_path).for_model(settings.model)
    if env_path is None or not env_path.is_file():
        raise ValueError("Paid providers require an explicitly specified .env file")
    if selected == "openai":
        from openai import OpenAI

        from models.openai_client import OpenAIClient

        api_key = dotenv_values(env_path, interpolate=False).get("OPENAI_API_KEY")
        if not api_key or not api_key.strip():
            raise ValueError("OPENAI_API_KEY is missing from the specified .env file")
        return OpenAIClient(
            sdk_client=OpenAI(
                api_key=api_key,
                max_retries=0,
                timeout=settings.timeout_ms / 1000,
                base_url="https://api.openai.com/v1",
            ),
            model=settings.model,
            price=price,
            run_log=run_log,
            max_output_tokens=settings.max_output_tokens,
            reasoning_effort=settings.thinking_level or "low",
        )
    if selected == "anthropic":
        from anthropic import Anthropic

        from models.anthropic_client import AnthropicClient

        api_key = dotenv_values(env_path, interpolate=False).get("CLAUDE_API_KEY")
        if not api_key or not api_key.strip():
            raise ValueError("CLAUDE_API_KEY is missing from the specified .env file")
        return AnthropicClient(
            sdk_client=Anthropic(
                api_key=api_key,
                max_retries=0,
                timeout=settings.timeout_ms / 1000,
                base_url="https://api.anthropic.com",
            ),
            model=settings.model,
            price=price,
            run_log=run_log,
            max_output_tokens=settings.max_output_tokens,
            effort=settings.thinking_level or "low",
        )
    from google import genai
    from google.genai import types

    from models.gemini import GeminiClient

    # No environment mutation, parent-directory search or ${ENV} substitution.
    api_key = dotenv_values(env_path, interpolate=False).get("GEMINI_API_KEY")
    if not api_key or not api_key.strip() or api_key == "your-gemini-api-key":
        raise ValueError("GEMINI_API_KEY is missing from the specified .env file")
    sdk = genai.Client(
        vertexai=False,
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=settings.timeout_ms,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    return GeminiClient(
        sdk_client=sdk,
        model=settings.model,
        price=price,
        run_log=run_log,
        temperature=settings.temperature,
        max_output_tokens=settings.max_output_tokens,
        thinking_level=settings.thinking_level,
    )
