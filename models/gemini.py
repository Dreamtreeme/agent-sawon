"""Google Gen AI SDK adapter: one generation request per measured call."""

from __future__ import annotations

import mimetypes
from decimal import Decimal
from pathlib import Path
from typing import Any

from google.genai import errors, types
from pydantic import BaseModel

from models.base import EventSink, MeasuredModelClient, ProviderReply, TokenUsage
from models.pricing import PriceRate

_SAFE_PROVIDER_STATUSES = frozenset(
    {
        "CANCELLED",
        "UNKNOWN",
        "INVALID_ARGUMENT",
        "DEADLINE_EXCEEDED",
        "NOT_FOUND",
        "ALREADY_EXISTS",
        "PERMISSION_DENIED",
        "UNAUTHENTICATED",
        "RESOURCE_EXHAUSTED",
        "FAILED_PRECONDITION",
        "ABORTED",
        "OUT_OF_RANGE",
        "UNIMPLEMENTED",
        "INTERNAL",
        "UNAVAILABLE",
        "DATA_LOSS",
    }
)


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
        thinking_level: str | None = None,
    ) -> None:
        super().__init__(provider="gemini", model=model, run_log=run_log)
        self._sdk = sdk_client
        self._price = price
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._thinking_level = thinking_level
        self.last_response: dict | None = None

    def _generate(
        self, prompt: str, schema: type[BaseModel], *, images: list[Path] | None
    ) -> ProviderReply:
        self.last_response = None
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
                thinking_config=types.ThinkingConfig(thinking_level=self._thinking_level)
                if self._thinking_level is not None
                else None,
                candidate_count=1,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
            ),
        )
        self.last_response = {
            "text": response.text or "",
            "finish_reasons": [
                c.finish_reason.value
                for c in (getattr(response, "candidates", None) or [])
                if isinstance(getattr(c, "finish_reason", None), types.FinishReason)
            ],
        }
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
        return ProviderReply(
            text=response.text or "",
            usage=usage,
            output_truncated=any(
                getattr(c, "finish_reason", None) == types.FinishReason.MAX_TOKENS
                for c in (getattr(response, "candidates", None) or [])
            ),
        )

    def _cost(self, usage: TokenUsage) -> Decimal:
        return self._price.cost(usage)

    def _failure_metadata(self, error: BaseException) -> dict[str, str | int]:
        if not isinstance(error, errors.APIError):
            return {}
        metadata: dict[str, str | int] = {}
        # These SDK attributes are scalars; never inspect .details, .message,
        # .response, request URLs, or headers, which may contain private inputs.
        code = error.code
        if type(code) is int and 100 <= code <= 599:
            metadata["http_status"] = code
            if code in {401, 403}:
                category = "authentication_or_permission"
            elif code == 429:
                category = "rate_limited"
            elif code in {408, 504}:
                category = "timeout"
            elif code >= 500:
                category = "server_error"
            elif code >= 400:
                category = "client_error"
            else:
                category = "http_error"
            metadata["error_category"] = category
        status = error.status
        if isinstance(status, str) and status in _SAFE_PROVIDER_STATUSES:
            metadata["provider_status"] = status
        return metadata
