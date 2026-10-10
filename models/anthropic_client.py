"""Claude Messages API adapter with measured structured-output calls."""

from __future__ import annotations

import base64
import json
import mimetypes
from decimal import Decimal
from pathlib import Path
from typing import Any

from anthropic import APIStatusError, transform_schema
from pydantic import BaseModel

from models.base import EventSink, MeasuredModelClient, ProviderReply, TokenUsage
from models.pricing import PriceRate


class AnthropicClient(MeasuredModelClient):
    def __init__(
        self,
        *,
        sdk_client: Any,
        model: str,
        price: PriceRate,
        run_log: EventSink,
        max_output_tokens: int = 8192,
        effort: str = "low",
    ) -> None:
        super().__init__(provider="anthropic", model=model, run_log=run_log)
        self._sdk, self._price = sdk_client, price
        self._max_output_tokens, self._effort = max_output_tokens, effort

    def _generate(
        self, prompt: str, schema: type[BaseModel], *, images: list[Path] | None
    ) -> ProviderReply:
        content: list[dict[str, Any]] = []
        for path in images or []:
            mime, _ = mimetypes.guess_type(path.name)
            if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                raise ValueError("Unsupported image MIME type")
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": prompt})
        response = self._sdk.messages.create(
            model=self.model,
            max_tokens=self._max_output_tokens,
            messages=[{"role": "user", "content": content}],
            thinking={"type": "adaptive"},
            output_config={
                "effort": self._effort,
                "format": {
                    "type": "json_schema",
                    "schema": transform_schema(schema),
                },
            },
        )
        usage_metadata = response.usage
        cache_write = getattr(usage_metadata, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage_metadata, "cache_read_input_tokens", 0) or 0
        usage = TokenUsage(
            input_tokens=usage_metadata.input_tokens + cache_write + cache_read,
            response_tokens=usage_metadata.output_tokens,
            cached_input_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        # Normalize whitespace only; Pydantic remains the final local contract authority.
        if text:
            text = json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
        return ProviderReply(
            text=text,
            usage=usage,
            output_truncated=response.stop_reason == "max_tokens",
        )

    def _cost(self, usage: TokenUsage) -> Decimal:
        price = self._price
        if self.model == "claude-haiku-5-5" and usage.input_tokens > 100_000:
            price = price.model_copy(
                update={
                    "input_usd_per_million": price.input_usd_per_million * 5,
                    "output_usd_per_million": price.output_usd_per_million * 5,
                    "cached_input_usd_per_million": price.cached_input_usd_per_million * 5
                    if price.cached_input_usd_per_million is not None
                    else None,
                    "cache_write_usd_per_million": price.cache_write_usd_per_million * 5
                    if price.cache_write_usd_per_million is not None
                    else None,
                }
            )
        return price.cost(usage)

    def _failure_metadata(self, error: BaseException) -> dict[str, str | int]:
        if isinstance(error, APIStatusError):
            return {"http_status": error.status_code}
        return {}
