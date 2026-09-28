"""Ordered, request-bound replay; no SDK, credentials, network or billed usage."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from models.base import (
    EventSink,
    MeasuredModelClient,
    ProviderReply,
    TokenUsage,
    request_fingerprint,
)


class ReplayMismatchError(ValueError):
    pass


class ReplayExhaustedError(RuntimeError):
    pass


class ReplayEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["0.1"] = "0.1"
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_json: str
    recorded_usage: TokenUsage | None = None
    recorded_usd: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


class ReplayClient(MeasuredModelClient):
    def __init__(self, entries: list[ReplayEntry], *, run_log: EventSink) -> None:
        super().__init__(provider="replay", model="replay", run_log=run_log)
        self._entries = tuple(entries)
        self._index = 0
        self._lock = Lock()

    @classmethod
    def from_jsonl(cls, path: Path, *, run_log: EventSink) -> ReplayClient:
        with path.open(encoding="utf-8") as handle:
            entries = [ReplayEntry.model_validate_json(line) for line in handle if line.strip()]
        return cls(entries, run_log=run_log)

    @property
    def remaining(self) -> int:
        return len(self._entries) - self._index

    def _generate(
        self, prompt: str, schema: type[BaseModel], *, images: list[Path] | None
    ) -> ProviderReply:
        fingerprint = request_fingerprint(prompt, schema, images)
        with self._lock:
            if self._index >= len(self._entries):
                raise ReplayExhaustedError("Replay has no remaining responses")
            entry = self._entries[self._index]
            if entry.request_sha256 != fingerprint:
                raise ReplayMismatchError("Replay request differs from its recorded request")
            self._index += 1
        return ProviderReply(
            text=entry.response_json,
            usage=TokenUsage(input_tokens=0, response_tokens=0),
            recorded_usage=entry.recorded_usage,
            recorded_usd=entry.recorded_usd,
        )

    def _cost(self, usage: TokenUsage) -> Decimal:
        return Decimal(0)
