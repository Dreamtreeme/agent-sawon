"""Append-only events and atomic, validated local case state.

Cooperating processes lock sidecar files; callers must never edit logs directly.
A partial/corrupt log is reported without rewriting evidence. This is local disk
coordination, not the distributed lease mechanism planned for M3.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

import portalocker
from pydantic import AwareDatetime, BaseModel, ConfigDict, JsonValue, ValidationError
from sawon_interfaces import EventCost, RunEvent


class LogIntegrityError(ValueError):
    """An existing log must be preserved for inspection instead of extended."""


class InvalidTransitionError(ValueError):
    """The requested case state transition is forbidden or stale."""


class CaseStatus(StrEnum):
    RECEIVED = "received"
    EXTRACTING = "extracting"
    READY = "ready"
    ENTERING = "entering"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.RECEIVED: frozenset({CaseStatus.EXTRACTING, CaseStatus.READY, CaseStatus.FAILED}),
    CaseStatus.EXTRACTING: frozenset({CaseStatus.READY, CaseStatus.FAILED}),
    CaseStatus.READY: frozenset({CaseStatus.ENTERING, CaseStatus.FAILED}),
    CaseStatus.ENTERING: frozenset({CaseStatus.AWAITING_APPROVAL, CaseStatus.FAILED}),
    CaseStatus.AWAITING_APPROVAL: frozenset({CaseStatus.APPROVED, CaseStatus.REJECTED}),
    CaseStatus.APPROVED: frozenset(),
    CaseStatus.REJECTED: frozenset({CaseStatus.READY}),
    CaseStatus.FAILED: frozenset(),
}


class CaseState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["0.1"] = "0.1"
    case_id: str
    status: CaseStatus
    updated_at: AwareDatetime
    reason: str | None = None


def atomic_write_json(path: Path, value: BaseModel | dict[str, JsonValue]) -> None:
    """Flush a complete sibling temporary file, then atomically replace `path`."""
    path = Path(path)
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _validate_identity(case_id: str, agent_id: str = "storage") -> None:
    RunEvent(seq=0, ts=datetime.now(UTC), case_id=case_id, agent_id=agent_id, kind="observe")


class RunLog:
    """Single-case event stream with cross-process sequence assignment."""

    def __init__(self, case_dir: Path, *, case_id: str, agent_id: str, lock_timeout: float = 10):
        _validate_identity(case_id, agent_id)
        self.case_dir = Path(case_dir)
        self.case_id = case_id
        self.agent_id = agent_id
        self.lock_timeout = lock_timeout
        self.case_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.case_dir / "run_log.jsonl"
        self.lock_path = self.case_dir / ".run_log.lock"

    def _read_unlocked(self) -> list[RunEvent]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LogIntegrityError(
                "Log has an incomplete final line; preserved without modification"
            )
        events: list[RunEvent] = []
        for index, line in enumerate(raw.splitlines()):
            try:
                event = RunEvent.model_validate_json(line)
            except ValidationError as exc:
                raise LogIntegrityError(f"Invalid event at line {index + 1}") from exc
            if event.seq != index or event.case_id != self.case_id:
                raise LogIntegrityError(f"Invalid sequence or case identity at line {index + 1}")
            events.append(event)
        return events

    def read_all(self) -> list[RunEvent]:
        with portalocker.Lock(self.lock_path, mode="a", timeout=self.lock_timeout):
            return self._read_unlocked()

    def append(
        self,
        kind: str,
        *,
        payload: dict[str, JsonValue] | None = None,
        cost: EventCost | None = None,
        latency_ms: float = 0,
    ) -> RunEvent:
        with portalocker.Lock(self.lock_path, mode="a", timeout=self.lock_timeout):
            previous = self._read_unlocked()
            event = RunEvent(
                seq=len(previous),
                ts=datetime.now(UTC),
                case_id=self.case_id,
                agent_id=self.agent_id,
                kind=kind,
                payload={} if payload is None else payload,
                cost=EventCost() if cost is None else EventCost.model_validate(cost.model_dump()),
                latency_ms=latency_ms,
            )
            with self.path.open("ab") as stream:
                stream.write((event.model_dump_json() + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            return event


class StateStore:
    """Case state with an explicit transition table and stale-update protection.

    This storage utility does not grant roles or approve a shipment. The M3
    approval service must authenticate the caller before requesting a transition.
    """

    def __init__(self, case_dir: Path, *, case_id: str, lock_timeout: float = 10):
        _validate_identity(case_id)
        self.case_id = case_id
        self.case_dir = Path(case_dir)
        self.case_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.case_dir / "status.json"
        self.lock_path = self.case_dir / ".status.lock"
        self.lock_timeout = lock_timeout

    def _read_unlocked(self) -> CaseState:
        state = CaseState.model_validate_json(self.path.read_bytes())
        if state.case_id != self.case_id:
            raise InvalidTransitionError("State belongs to a different case")
        return state

    def initialize(self) -> CaseState:
        with portalocker.Lock(self.lock_path, mode="a", timeout=self.lock_timeout):
            if self.path.exists():
                return self._read_unlocked()
            state = CaseState(
                case_id=self.case_id, status=CaseStatus.RECEIVED, updated_at=datetime.now(UTC)
            )
            atomic_write_json(self.path, state)
            return state

    def read(self) -> CaseState:
        with portalocker.Lock(self.lock_path, mode="a", timeout=self.lock_timeout):
            return self._read_unlocked()

    def transition(
        self,
        target: CaseStatus | str,
        *,
        expected: CaseStatus | str,
        reason: str | None = None,
    ) -> CaseState:
        target, expected = CaseStatus(target), CaseStatus(expected)
        with portalocker.Lock(self.lock_path, mode="a", timeout=self.lock_timeout):
            current = self._read_unlocked()
            if current.status != expected:
                raise InvalidTransitionError(f"Expected {expected}, found {current.status}")
            if target not in TRANSITIONS[current.status]:
                raise InvalidTransitionError(f"Forbidden transition: {current.status} -> {target}")
            state = CaseState(
                case_id=self.case_id, status=target, updated_at=datetime.now(UTC), reason=reason
            )
            atomic_write_json(self.path, state)
            return state
