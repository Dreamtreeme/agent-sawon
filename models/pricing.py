"""Explicit price tables; never silently substitute zero for unknown prices."""

from __future__ import annotations

import tomllib
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from models.base import TokenUsage


class PricingError(ValueError):
    pass


class PriceRate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_usd_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    output_usd_per_million: Decimal = Field(ge=0, allow_inf_nan=False)
    cached_input_usd_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_write_usd_per_million: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)

    def cost(self, usage: TokenUsage) -> Decimal:
        if usage.cached_input_tokens and self.cached_input_usd_per_million is None:
            raise PricingError("Cached input has no configured price")
        if usage.cache_write_tokens and self.cache_write_usd_per_million is None:
            raise PricingError("Cache writes have no configured price")
        uncached = usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens
        total = Decimal(uncached) * self.input_usd_per_million
        total += Decimal(usage.output_tokens) * self.output_usd_per_million
        if usage.cached_input_tokens:
            total += Decimal(usage.cached_input_tokens) * self.cached_input_usd_per_million
        if usage.cache_write_tokens:
            total += Decimal(usage.cache_write_tokens) * self.cache_write_usd_per_million
        return total / Decimal(1_000_000)


class PriceBook(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["0.1"] = "0.1"
    checked_on: date
    source: str
    basis: Literal["standard_paid_text_image"]
    models: dict[str, PriceRate]

    @classmethod
    def load(cls, path: Path) -> PriceBook:
        with path.open("rb") as handle:
            return cls.model_validate(tomllib.load(handle))

    def for_model(self, model: str) -> PriceRate:
        try:
            return self.models[model]
        except KeyError as exc:
            raise PricingError(f"No configured price for model: {model}") from exc
