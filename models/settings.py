"""Model configuration contract, independent of SDK construction."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["0.1"] = "0.1"
    provider: Literal["anthropic", "gemini", "replay", "openai"] = "gemini"
    model: str = Field(default="gemini-3.8-flash", min_length=1)
    pricing_path: str = "pricing.toml"
    replay_path: str | None = None
    temperature: float = Field(default=0, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int = Field(default=4096, gt=0)
    thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    timeout_ms: int = Field(default=60_000, gt=0)
