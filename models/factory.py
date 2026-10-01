"""Config-driven clients; secrets are loaded only from an explicit .env file."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from models.base import EventSink, ModelClient
from models.gemini import GeminiClient
from models.jev import JevClient
from models.pricing import PriceBook
from models.replay import ReplayClient


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["0.1"] = "0.1"
    provider: Literal["gemini", "replay", "jev"] = "gemini"
    model: str = Field(default="gemini-3.8-flash", min_length=1)
    pricing_path: str = "pricing.toml"
    replay_path: str = "../examples/replay_responses.jsonl"
    temperature: float = Field(default=0, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int = Field(default=4096, gt=0)
    timeout_ms: int = Field(default=60_000, gt=0)


def create_model_client(
    config_path: Path,
    *,
    run_log: EventSink,
    env_path: Path | None = None,
    provider: Literal["gemini", "replay", "jev"] | None = None,
) -> ModelClient:
    """Replay needs no .env; Gemini never falls back to ambient credentials."""
    with config_path.open("rb") as handle:
        settings = ModelSettings.model_validate(tomllib.load(handle))
    selected = provider if provider is not None else settings.provider
    if selected not in {"gemini", "replay", "jev"}:
        raise ValueError("Unknown model provider")
    if selected == "replay":
        return ReplayClient.from_jsonl(config_path.parent / settings.replay_path, run_log=run_log)
    price = PriceBook.load(config_path.parent / settings.pricing_path).for_model(settings.model)
    if env_path is None or not env_path.is_file():
        raise ValueError("Paid providers require an explicitly specified .env file")
    if selected == "jev":
        secrets = dotenv_values(env_path, interpolate=False)
        api_key = secrets.get("TYPESAFE_API_KEY") or secrets.get("JEV_API_KEY")
        if not api_key or not api_key.strip():
            raise ValueError(
                "TYPESAFE_API_KEY or JEV_API_KEY is missing from the specified .env file"
            )
        return JevClient(
            api_key=api_key,
            model=settings.model,
            price=price,
            run_log=run_log,
            timeout_seconds=settings.timeout_ms / 1000,
        )
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
    )
