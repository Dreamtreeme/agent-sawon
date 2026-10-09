"""Verified, exact visual match reuse before any paid screen-model call."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import portalocker
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sawon_runtime.storage import atomic_write_json


class VisualMatchCacheIntegrityError(ValueError):
    """The private cache is invalid and must be preserved for inspection."""


class VisualMatchEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    response: dict[str, Any]
    success_count: int = Field(default=1, ge=1)


class VisualMatchDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["0.1"] = "0.1"
    entries: tuple[VisualMatchEntry, ...] = ()


def visual_match_key(state: dict[str, Any]) -> str:
    """Bind reuse to current pixels/catalog plus the complete workflow contract."""
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


class VisualMatchCache:
    def __init__(self, path: Path, *, enabled: bool = False, lock_timeout: float = 10):
        self.path = Path(path)
        self.enabled = enabled
        self.lock_timeout = lock_timeout
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._staged: dict[str, VisualMatchEntry] = {}

    def _read_unlocked(self) -> VisualMatchDocument:
        if not self.path.exists():
            return VisualMatchDocument()
        try:
            return VisualMatchDocument.model_validate_json(self.path.read_bytes())
        except (ValidationError, UnicodeDecodeError) as exc:
            raise VisualMatchCacheIntegrityError(
                "Invalid visual match cache; preserved without modification"
            ) from exc

    def lookup(self, state: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        if not self.enabled:
            return None
        key = visual_match_key(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(self.lock_path, mode="a", timeout=self.lock_timeout):
            entry = next((item for item in self._read_unlocked().entries if item.key == key), None)
        return None if entry is None else (key, entry.response)

    def stage(self, key: str, response: dict[str, Any]) -> None:
        if self.enabled:
            self._staged[key] = VisualMatchEntry(key=key, response=response)

    def finish(self, *, success: bool) -> int:
        staged = tuple(self._staged.values())
        self._staged.clear()
        if not self.enabled or not success or not staged:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(self.lock_path, mode="a", timeout=self.lock_timeout):
            existing = {item.key: item for item in self._read_unlocked().entries}
            for item in staged:
                previous = existing.get(item.key)
                existing[item.key] = item.model_copy(
                    update={"success_count": 1 if previous is None else previous.success_count + 1}
                )
            atomic_write_json(
                self.path,
                VisualMatchDocument(entries=tuple(existing[key] for key in sorted(existing))),
            )
        return len(staged)
