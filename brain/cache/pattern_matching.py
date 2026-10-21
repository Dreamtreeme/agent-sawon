"""Pattern Matching: exact normalized screen/task lookup before a paid model call.

Coordinate memories also use pHash matching in methods._coordinate_bindings.
This cache retains its exact semantic contract; renaming does not broaden matching.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import portalocker
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sawon_runtime.storage import atomic_write_json


class ScreenPatternCacheIntegrityError(ValueError):
    """The private cache is invalid and must be preserved for inspection."""


class ScreenPatternEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    response: dict[str, Any]
    success_count: int = Field(default=1, ge=1)


class ScreenPatternDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["0.1"] = "0.1"
    entries: tuple[ScreenPatternEntry, ...] = ()


_VOLATILE_KEYS = {
    "capture_id",
    "image_path",
    "obs_id",
    "observation_id",
    "ref",
    "revision",
    "screenshot",
}
_DIAGNOSTIC_NOTEBOOK_KEYS = {
    "recent_feedback",
    "recent_turns",
    "scratchpad",
}
_CONFIDENCE_KEYS = {"candidate_confidence", "confidence"}
_CACHE_LAYER_KEYS = {"method_memory", "observation_route"}


def _cache_contract(value: Any, *, parent: str | None = None) -> Any:
    """Remove run-local metadata while retaining every decision-bearing fact."""

    if isinstance(value, dict):
        if parent == "inputs":
            return sorted(value)
        result = {}
        for key, item in value.items():
            if key in _VOLATILE_KEYS or key in _CONFIDENCE_KEYS or key in _CACHE_LAYER_KEYS:
                continue
            if parent == "notebook" and key in _DIAGNOSTIC_NOTEBOOK_KEYS:
                continue
            result[key] = _cache_contract(item, parent=key)
        return result
    if isinstance(value, list):
        return [_cache_contract(item, parent=parent) for item in value]
    if isinstance(value, tuple):
        return tuple(_cache_contract(item, parent=parent) for item in value)
    return value


def screen_pattern_key(state: dict[str, Any]) -> str:
    """Bind reuse to stable screen semantics and the executable task contract."""
    contract = _cache_contract(state)
    encoded = json.dumps(
        contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ScreenPatternCache:
    def __init__(self, path: Path, *, enabled: bool = False, lock_timeout: float = 10):
        self.path = Path(path)
        self.enabled = enabled
        self.lock_timeout = lock_timeout
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._staged: dict[str, ScreenPatternEntry] = {}

    def _read_unlocked(self) -> ScreenPatternDocument:
        if not self.path.exists():
            return ScreenPatternDocument()
        try:
            return ScreenPatternDocument.model_validate_json(self.path.read_bytes())
        except (ValidationError, UnicodeDecodeError) as exc:
            raise ScreenPatternCacheIntegrityError(
                "Invalid visual match cache; preserved without modification"
            ) from exc

    def lookup(self, state: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        if not self.enabled:
            return None
        key = screen_pattern_key(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(self.lock_path, mode="a", timeout=self.lock_timeout):
            entry = next((item for item in self._read_unlocked().entries if item.key == key), None)
        return None if entry is None else (key, entry.response)

    def stage(self, key: str, response: dict[str, Any]) -> None:
        if self.enabled:
            self._staged[key] = ScreenPatternEntry(key=key, response=response)

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
                ScreenPatternDocument(entries=tuple(existing[key] for key in sorted(existing))),
            )
        return len(staged)
