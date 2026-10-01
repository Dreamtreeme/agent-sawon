"""Responses API adapter with one request per measured call."""

from __future__ import annotations

import base64
import mimetypes
from decimal import Decimal
from pathlib import Path
from typing import Any

from openai import APIStatusError, pydantic_function_tool
from pydantic import BaseModel

from models.base import EventSink, MeasuredModelClient, ProviderReply, TokenUsage
from models.pricing import PriceRate


class OpenAIClient(MeasuredModelClient):
    def __init__(
        self,
        *,
        sdk_client: Any,
        model: str,
        price: PriceRate,
        run_log: EventSink,
        max_output_tokens: int = 8192,
        reasoning_effort: str = "low",
    ) -> None:
        super().__init__(provider="openai", model=model, run_log=run_log)
        self._sdk, self._price = sdk_client, price
        self._max_output_tokens, self._reasoning_effort = max_output_tokens, reasoning_effort
        self.last_response: dict | None = None

    def _generate(
        self, prompt: str, schema: type[BaseModel], *, images: list[Path] | None
    ) -> ProviderReply:
        self.last_response = None
        content = [{"type": "input_text", "text": prompt}]
        for path in images or []:
            mime, _ = mimetypes.guess_type(path.name)
            if mime not in {"image/png", "image/jpeg", "image/webp"}:
                raise ValueError("Unsupported image MIME type")
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{mime};base64,{encoded}",
                    "detail": "original",
                }
            )
        # Use the SDK's public Pydantic schema conversion; parse locally AFTER accounting.
        parameters = pydantic_function_tool(schema)["function"]["parameters"]
        response = self._sdk.responses.create(
            model=self.model,
            input=[{"role": "user", "content": content}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema.__name__,
                    "schema": parameters,
                    "strict": True,
                }
            },
            reasoning={"effort": self._reasoning_effort},
            max_output_tokens=self._max_output_tokens,
            store=False,
        )
        incomplete = getattr(response, "incomplete_details", None)
        self.last_response = {
            "text": response.output_text or "",
            "status": response.status,
            "incomplete_reason": getattr(incomplete, "reason", None),
        }
        metadata = response.usage
        usage = None
        if metadata is not None:
            thinking = getattr(metadata.output_tokens_details, "reasoning_tokens", 0) or 0
            usage = TokenUsage(
                input_tokens=metadata.input_tokens,
                response_tokens=metadata.output_tokens - thinking,
                thinking_tokens=thinking,
                cached_input_tokens=getattr(metadata.input_tokens_details, "cached_tokens", 0) or 0,
                cache_write_tokens=getattr(metadata.input_tokens_details, "cache_write_tokens", 0)
                or 0,
            )
        # Incomplete/refused results cannot become valid actions even if a JSON fragment parses.
        return ProviderReply(
            text=response.output_text if response.status == "completed" else "",
            usage=usage,
            output_truncated=getattr(incomplete, "reason", None) == "max_output_tokens",
        )

    def _cost(self, usage: TokenUsage) -> Decimal:
        price = self._price
        if self.model == "gpt-5.6-luna" and usage.input_tokens > 272_000:
            price = price.model_copy(
                update={
                    "input_usd_per_million": price.input_usd_per_million * 2,
                    "output_usd_per_million": price.output_usd_per_million * Decimal("1.5"),
                    "cached_input_usd_per_million": price.cached_input_usd_per_million * 2
                    if price.cached_input_usd_per_million is not None
                    else None,
                    "cache_write_usd_per_million": price.cache_write_usd_per_million * 2
                    if price.cache_write_usd_per_million is not None
                    else None,
                }
            )
        return price.cost(usage)

    def _failure_metadata(self, error: BaseException) -> dict[str, str | int]:
        if isinstance(error, APIStatusError):
            return {"http_status": error.status_code}
        return {}
