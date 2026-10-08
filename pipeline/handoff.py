"""Validate the measured document-extraction handoff before execution."""

from pathlib import Path

from sawon_interfaces import ShipmentRecord

from brain.runtime.storage import CaseState, CaseStatus, RunLog


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
