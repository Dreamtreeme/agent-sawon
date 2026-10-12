"""Typed model boundary and mandatory, failure-aware call accounting."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sawon_interfaces import EventCost, RunEvent

from .validation import safe_validation_errors

T = TypeVar("T", bound=BaseModel)

_CALL_CONTEXT: ContextVar[dict[str, object] | None] = ContextVar("model_call_context", default=None)
_REQUEST_METADATA: ContextVar[dict | None] = ContextVar("model_request_metadata", default=None)


def record_wire_schema(schema: dict, *, codec: str) -> None:
    """Record the actual provider schema without exposing prompt or response contents."""
    metadata = _REQUEST_METADATA.get()
    if metadata is None:
        return

    def unions(node) -> int:
        if isinstance(node, dict):
            return int("anyOf" in node or isinstance(node.get("type"), list)) + sum(
                unions(child) for child in node.values()
            )
        return sum(unions(child) for child in node) if isinstance(node, list) else 0

    encoded = json.dumps(schema, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    metadata.update(
        wire_schema_sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        wire_schema_utf8_bytes=len(encoded.encode("utf-8")),
        wire_schema_codec=codec,
        wire_schema_union_count=unions(schema),
    )


@contextmanager
def model_call_context(**metadata: object) -> Iterator[None]:
    """Attach caller-owned provenance without changing provider/replay contracts."""
    token = _CALL_CONTEXT.set({**(_CALL_CONTEXT.get() or {}), **metadata})
    try:
        yield
    finally:
        _CALL_CONTEXT.reset(token)


class TokenUsage(BaseModel):
    """Prompt tokens include cached tokens; output includes response plus thinking."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    input_tokens: int = Field(ge=0, strict=True)
    response_tokens: int = Field(ge=0, strict=True)
    thinking_tokens: int = Field(default=0, ge=0, strict=True)
    cached_input_tokens: int = Field(default=0, ge=0, strict=True)
    cache_write_tokens: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def check_cached_count(self) -> TokenUsage:
        if self.cached_input_tokens + self.cache_write_tokens > self.input_tokens:
            raise ValueError("Cached input cannot exceed all input tokens")
        return self

    @property
    def output_tokens(self) -> int:
        return self.response_tokens + self.thinking_tokens


@dataclass(frozen=True)
class ModelResult[T: BaseModel]:
    response: T
    usage: TokenUsage
    usd: Decimal
    latency_ms: float
    provider: str
    model: str
    recorded_usage: TokenUsage | None = None
    recorded_usd: Decimal | None = None


class ModelClient(Protocol):
    def complete_json(
        self, prompt: str, schema: type[T], *, images: list[Path] | None = None
    ) -> ModelResult[T]: ...


class EventSink(Protocol):
    """Implemented by sawon_runtime.storage.RunLog; kept structurally typed."""

    def append(
        self,
        kind: str,
        *,
        payload: dict | None = None,
        cost: EventCost | None = None,
        latency_ms: float = 0,
    ) -> RunEvent: ...


class UsageUnavailableError(RuntimeError):
    """An otherwise completed response lacked trustworthy usage metadata."""


@dataclass(frozen=True)
class ProviderReply:
    text: str
    usage: TokenUsage | None
    recorded_usage: TokenUsage | None = None
    recorded_usd: Decimal | None = None
    output_truncated: bool = False


def request_fingerprint(prompt: str, schema: type[BaseModel], images: list[Path] | None) -> str:
    """Bind replay entries to prompt, full schema and ordered image content."""
    body = {
        "prompt": prompt,
        "schema": schema.model_json_schema(),
        "images": [hashlib.sha256(path.read_bytes()).hexdigest() for path in images or []],
    }
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class MeasuredModelClient(ABC):
    """Every public call appends one event, even SDK, parsing and replay failures.

    SDK retries must be disabled: otherwise a single wrapper invocation can hide
    multiple billable requests. Failure messages and prompt contents are omitted
    from logs because third-party exceptions may contain credentials or inputs.
    """

    def __init__(self, *, provider: str, model: str, run_log: EventSink) -> None:
        self.provider = provider
        self.model = model
        self._run_log = run_log

    def complete_json(
        self, prompt: str, schema: type[T], *, images: list[Path] | None = None
    ) -> ModelResult[T]:
        started = perf_counter()
        reply: ProviderReply | None = None
        api_free = self.provider in {"replay", "local"}
        usage = TokenUsage(input_tokens=0, response_tokens=0) if api_free else None
        usd = Decimal(0) if api_free else None
        payload = {
            "call_id": str(uuid4()),
            "context": dict(_CALL_CONTEXT.get() or {}),
            "response_schema": schema.__name__,
            "prompt_utf8_bytes": len(prompt.encode("utf-8")),
            "schema_utf8_bytes": len(json.dumps(schema.model_json_schema()).encode("utf-8")),
            "image_count": len(images or []),
            "provider": self.provider,
            "model": self.model,
            "status": "error",
            "usage_source": (
                "local_no_tokens"
                if self.provider == "local"
                else "replay_actual_zero"
                if self.provider == "replay"
                else "provider"
            ),
            "cost_basis": "no_api_call" if api_free else "configured_list_price",
        }
        request_metadata: dict = {}
        metadata_token = _REQUEST_METADATA.set(request_metadata)
        try:
            payload["request_sha256"] = request_fingerprint(prompt, schema, images)
            reply = self._generate(prompt, schema, images=images)
            usage = reply.usage
            if usage is None:
                raise UsageUnavailableError("Provider response did not include usage metadata")
            usd = self._cost(usage)
            response = schema.model_validate_json(reply.text)
            payload["status"] = "success"
        except BaseException as exc:
            payload["error_type"] = type(exc).__name__
            if isinstance(exc, ValidationError):
                payload["validation_errors"] = safe_validation_errors(exc, schema)
            # Provider adapters may expose a small allowlisted metadata set, but
            # never exception messages, response bodies, URLs, or request headers.
            payload.update(self._failure_metadata(exc))
            raise
        finally:
            payload.update(request_metadata)
            _REQUEST_METADATA.reset(metadata_token)
            latency_ms = (perf_counter() - started) * 1000
            payload["usage"] = usage.model_dump() if usage is not None else None
            if reply is not None and reply.output_truncated:
                payload["output_truncated"] = True
            if reply is not None and (
                reply.recorded_usage is not None or reply.recorded_usd is not None
            ):
                payload["recorded_usage"] = (
                    reply.recorded_usage.model_dump() if reply.recorded_usage is not None else None
                )
                payload["recorded_usd"] = (
                    str(reply.recorded_usd) if reply.recorded_usd is not None else None
                )
            self._run_log.append(
                "model_call",
                payload=payload,
                cost=EventCost(
                    input_tokens=usage.input_tokens if usage is not None else None,
                    output_tokens=usage.output_tokens if usage is not None else None,
                    usd=float(usd) if usd is not None else None,
                ),
                latency_ms=latency_ms,
            )
        return ModelResult(
            response=response,
            usage=usage,
            usd=usd,
            latency_ms=latency_ms,
            provider=self.provider,
            model=self.model,
            recorded_usage=reply.recorded_usage,
            recorded_usd=reply.recorded_usd,
        )

    @abstractmethod
    def _generate(
        self, prompt: str, schema: type[BaseModel], *, images: list[Path] | None
    ) -> ProviderReply: ...

    @abstractmethod
    def _cost(self, usage: TokenUsage) -> Decimal: ...

    def _failure_metadata(self, error: BaseException) -> dict[str, str | int]:
        return {}
