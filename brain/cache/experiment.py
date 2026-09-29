"""Explicit cold or fixed-snapshot cache copies for a new private execution case.

This does not orchestrate learning sequences or install demonstration proposals.
The supplied snapshot is read once and never opened through SemanticCache, so it
is not rewritten and no source lock file is created. Only the working copy may
be mutated by the brain. Abrupt process death leaves unfinished provenance.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sawon_interfaces import ShipmentRecord

from brain.runtime.storage import CaseState, CaseStatus, RunLog, atomic_write_json
from brain.settings import BrainSettings

from .semantic import CacheDocument

CacheIsolation = Literal["cold", "snapshot"]
CacheOutcome = Literal["initializing", "running", "success", "stopped", "failed", "interrupted"]


class CacheProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["0.1", "0.2"] = "0.2"
    mode: CacheIsolation
    prepared_case: bool = False
    preparation_log_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    preparation_event_count: int | None = Field(default=None, ge=0)
    preparation_log_preserved: bool | None = None
    snapshot_path: str | None = None
    snapshot_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    initial_path: Literal["cache-experiment/initial.json"] = "cache-experiment/initial.json"
    working_path: Literal["cache-experiment/working.json"] = "cache-experiment/working.json"
    initial_working_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    final_working_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    outcome: CacheOutcome = "initializing"
    created_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    complete: bool = False
    initial_cache_valid: bool | None = None
    final_cache_valid: bool | None = None
    error_type: str | None = None
    finalization_error_type: str | None = None


def validate_prepared_case(case_dir: Path, record: ShipmentRecord) -> None:
    """Validate an extraction handoff without changing its record, state or log."""
    from pipeline.checks import can_enter
    from pipeline.schemas import CheckReport

    state = CaseState.model_validate_json((case_dir / "status.json").read_bytes())
    saved = ShipmentRecord.model_validate_json((case_dir / "record.json").read_bytes())
    report = CheckReport.model_validate_json((case_dir / "checks.json").read_bytes())
    if (
        state.case_id != record.case_id
        or state.status != CaseStatus.READY
        or saved != record
        or not can_enter(record, report)
    ):
        raise ValueError("Prepared extraction case must be ready and use its checked record")
    if (case_dir / "result.json").exists():
        raise ValueError("An execution result already exists")
    if not (case_dir / "run_log.jsonl").is_file():
        raise ValueError("Prepared extraction requires its original measured log")
    if not RunLog(case_dir, case_id=record.case_id, agent_id="brain").read_all():
        raise ValueError("Prepared extraction requires nonempty measured evidence")


class CacheExperiment:
    """Single-use cache isolation; callers must finalize in their execution finally.

    Construction has no filesystem effects. start() requires a new execution
    case, preserves an immutable initial document, and returns copied settings.
    finish() records failures without changing business result/status or logs.
    """

    def __init__(
        self,
        settings: BrainSettings,
        *,
        case_dir: Path,
        mode: CacheIsolation,
        snapshot: Path | None = None,
    ) -> None:
        if not settings.cache.enabled:
            raise ValueError("Cache isolation requires cache.enabled=true")
        if mode not in {"cold", "snapshot"}:
            raise ValueError("Unsupported cache isolation mode")
        if (mode == "snapshot") != (snapshot is not None):
            raise ValueError("Only snapshot isolation requires an explicit snapshot")
        self.settings = settings
        self.case_dir = Path(case_dir).resolve()
        self.directory = self.case_dir / "cache-experiment"
        self.snapshot = Path(snapshot).resolve() if snapshot is not None else None
        if self.snapshot is not None and self.snapshot.is_relative_to(self.case_dir):
            raise ValueError("Cache snapshot must be outside the new execution case")
        self.working_path = self.directory / "working.json"
        self.provenance_path = self.directory / "provenance.json"
        self.provenance = CacheProvenance(
            mode=mode,
            snapshot_path=str(self.snapshot) if self.snapshot is not None else None,
            created_at=datetime.now(UTC),
        )
        self.started = False
        self.finished = False

    def start(self, *, prepared_record: ShipmentRecord | None = None) -> BrainSettings:
        if self.started or not self.case_dir.is_dir():
            raise ValueError("Cache isolation requires a new execution case")
        if prepared_record is not None:
            validate_prepared_case(self.case_dir, prepared_record)
            raw = (self.case_dir / "run_log.jsonl").read_bytes()
            self.provenance = self.provenance.model_copy(
                update={
                    "prepared_case": True,
                    "preparation_log_sha256": hashlib.sha256(raw).hexdigest(),
                    "preparation_event_count": len(raw.splitlines()),
                }
            )
        elif any(
            (self.case_dir / name).exists()
            for name in ("run_log.jsonl", "status.json", "result.json")
        ):
            raise ValueError("Cache isolation cannot reset an existing execution")
        # Exclusive ownership precedes all artifact writes. Existing evidence is
        # never replaced, even when it is empty, corrupt, or from an interrupted run.
        self.directory.mkdir(exist_ok=False)
        self.started = True
        try:
            atomic_write_json(self.provenance_path, self.provenance)
            document = CacheDocument()
            if self.snapshot is not None:
                raw = self.snapshot.read_bytes()
                self.provenance = self.provenance.model_copy(
                    update={"snapshot_sha256": hashlib.sha256(raw).hexdigest()}
                )
                # Proposal bundles and arbitrary extra fields are not cache snapshots.
                document = CacheDocument.model_validate_json(raw)
            atomic_write_json(self.directory / "initial.json", document)
            atomic_write_json(self.working_path, document)
            self.provenance = self.provenance.model_copy(
                update={
                    "initial_working_sha256": hashlib.sha256(
                        self.working_path.read_bytes()
                    ).hexdigest(),
                    "outcome": "running",
                }
            )
            atomic_write_json(self.provenance_path, self.provenance)
        except BaseException as error:
            self.finish(
                outcome="failed" if isinstance(error, Exception) else "interrupted",
                error_type=type(error).__name__,
            )
            raise
        return self.settings.model_copy(
            update={"cache": self.settings.cache.model_copy(update={"path": self.working_path})}
        )

    def finish(self, *, outcome: CacheOutcome, error_type: str | None = None) -> bool:
        """Return whether cache provenance is complete, even for a failed business run."""
        if not self.started:
            return False
        if self.finished:
            return self.provenance.complete
        if outcome in {"initializing", "running"}:
            raise ValueError("Cache experiment finalization requires a terminal outcome")
        final_hash = None
        valid = False
        finalization_error = None
        try:
            raw = self.working_path.read_bytes()
            final_hash = hashlib.sha256(raw).hexdigest()
            CacheDocument.model_validate_json(raw)
            valid = True
        except Exception as error:
            finalization_error = type(error).__name__
        initial_valid = False
        try:
            initial_raw = (self.directory / "initial.json").read_bytes()
            if hashlib.sha256(initial_raw).hexdigest() != self.provenance.initial_working_sha256:
                raise ValueError("Initial cache evidence changed")
            CacheDocument.model_validate_json(initial_raw)
            initial_valid = True
        except Exception as error:
            finalization_error = finalization_error or type(error).__name__
        preparation_preserved = None
        if self.provenance.prepared_case:
            preparation_preserved = False
            try:
                raw = (self.case_dir / "run_log.jsonl").read_bytes()
                count = self.provenance.preparation_event_count
                if count is None:
                    raise ValueError("Preparation log count is missing")
                prefix = b"".join(raw.splitlines(keepends=True)[:count])
                if hashlib.sha256(prefix).hexdigest() != self.provenance.preparation_log_sha256:
                    raise ValueError("Preparation log evidence changed")
                preparation_preserved = True
            except Exception as error:
                finalization_error = finalization_error or type(error).__name__
        self.provenance = self.provenance.model_copy(
            update={
                "outcome": outcome,
                "finished_at": datetime.now(UTC),
                "final_working_sha256": final_hash,
                "initial_cache_valid": initial_valid,
                "final_cache_valid": valid,
                "complete": valid and initial_valid and preparation_preserved is not False,
                "preparation_log_preserved": preparation_preserved,
                "error_type": error_type,
                "finalization_error_type": finalization_error,
            }
        )
        atomic_write_json(self.provenance_path, self.provenance)
        self.finished = True
        return self.provenance.complete
