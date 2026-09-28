"""Google Gen AI SDK adapter: one generation request per measured call."""

from __future__ import annotations

import mimetypes
from decimal import Decimal
from pathlib import Path
from typing import Any

from google.genai import types
from pydantic import BaseModel

from models.base import EventSink, MeasuredModelClient, ProviderReply, TokenUsage
from models.pricing import PriceRate


class GeminiClient(MeasuredModelClient):
    def __init__(
        self,
        *,
        sdk_client: Any,
        model: str,
        price: PriceRate,
        run_log: EventSink,
        temperature: float = 0,
        max_output_tokens: int = 4096,
    ) -> None:
        super().__init__(provider="gemini", model=model, run_log=run_log)
        self._sdk = sdk_client
        self._price = price
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

    def _generate(
        self, prompt: str, schema: type[BaseModel], *, images: list[Path] | None
    ) -> ProviderReply:
        parts = [types.Part.from_text(text=prompt)]
        for path in images or []:
            mime_type, _ = mimetypes.guess_type(path.name)
            if mime_type not in {
                "image/png",
                "image/jpeg",
                "image/webp",
                "image/heic",
                "image/heif",
            }:
                raise ValueError("Unsupported image MIME type")
            parts.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type))
        response = self._sdk.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=schema.model_json_schema(),
                temperature=self._temperature,
                max_output_tokens=self._max_output_tokens,
                candidate_count=1,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
            ),
        )
        metadata = response.usage_metadata
        usage = None
        if (
            metadata is not None
            and metadata.prompt_token_count is not None
            and metadata.candidates_token_count is not None
        ):
            usage = TokenUsage(
                input_tokens=metadata.prompt_token_count,
                response_tokens=metadata.candidates_token_count,
                thinking_tokens=metadata.thoughts_token_count or 0,
                cached_input_tokens=metadata.cached_content_token_count or 0,
            )
        return ProviderReply(text=response.text or "", usage=usage)

    def _cost(self, usage: TokenUsage) -> Decimal:
        return self._price.cost(usage)
