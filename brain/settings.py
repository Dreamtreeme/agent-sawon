"""Configurable limits for the private, relay-only agent."""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CacheSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    path: Path = Path("cases/semantic-cache.json")
    max_retries: int = Field(default=1, ge=0, le=3)


class BrainSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["0.1"] = "0.1"
    summary_strategy: Literal["flat_fields", "label_grouped"] = "label_grouped"
    max_steps: int = Field(default=80, ge=1, le=500)
    max_subgoal_failures: int = Field(default=2, ge=1, le=10)
    max_cost_usd: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    model_call_reserve_usd: float = Field(default=0.025, ge=0, allow_inf_nan=False)
    http_timeout_seconds: float = Field(default=30, gt=0, le=120)
    observation_max_depth: int = Field(default=12, ge=1, le=30)
    max_summary_elements: int = Field(default=500, ge=20, le=2000)
    navigation_history_size: int = Field(default=12, ge=4, le=64)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    recovery_enabled: bool = False

    @classmethod
    def load(cls, path: Path) -> "BrainSettings":
        with path.open("rb") as stream:
            settings = cls.model_validate(tomllib.load(stream))
        if not settings.cache.path.is_absolute():
            settings = settings.model_copy(
                update={
                    "cache": settings.cache.model_copy(
                        update={"path": (path.parent / settings.cache.path).resolve()}
                    )
                }
            )
        return settings
