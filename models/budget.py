"""Conservative configured-price reservation without a provider API call."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel

from models.factory import ModelSettings
from models.pricing import PriceBook, PriceRate


@dataclass(frozen=True)
class CallBudget:
    price: PriceRate
    max_output_tokens: int

    @classmethod
    def from_config(cls, path: Path) -> CallBudget | None:
        with path.open("rb") as stream:
            settings = ModelSettings.model_validate(tomllib.load(stream))
        if settings.provider == "replay":
            return None
        return cls(
            PriceBook.load(path.parent / settings.pricing_path).for_model(settings.model),
            settings.max_output_tokens,
        )

    def __call__(self, prompt: str, schema: type[BaseModel]) -> Decimal:
        # Text-only brain: one token per UTF-8 byte overestimates ordinary text
        # tokenization. Include schema bytes and a 1024-token request-envelope
        # allowance. No hidden retries; max_output_tokens includes thinking.
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        input_bound = len(prompt.encode("utf-8")) + len(schema_json.encode("utf-8")) + 1024
        return (
            input_bound * self.price.input_usd_per_million
            + self.max_output_tokens * self.price.output_usd_per_million
        ) / Decimal(1_000_000)
