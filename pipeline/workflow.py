"""Black-box harness entry point: document extraction, checks, then ERP execution.

The harness record is an identity envelope only. Its expected field values are
never validated, copied, compared, or passed to the pipeline/model/brain. Only
the explicitly supplied input manifest provides shipment evidence.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from sawon_interfaces import CaseId

from brain.graph import Result
from brain.run import main as run_brain
from brain.runtime.storage import CaseStatus, RunLog, StateStore, atomic_write_json
from brain.settings import BrainSettings
from models.budget import ImageCallBudget
from models.factory import create_model_client

from .runner import load_inputs, prepare_case
from .schemas import PipelineSettings


class _HarnessIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    case_id: CaseId


def _terminal_failure(case_dir: Path, case_id: str, reason: str, started: float) -> None:
    """Preserve any terminal result and append to the existing measured log."""
    if (case_dir / "result.json").exists():
        return
    state = StateStore(case_dir, case_id=case_id)
    current = state.initialize()
    if current.status in {
        CaseStatus.RECEIVED,
        CaseStatus.EXTRACTING,
        CaseStatus.READY,
        CaseStatus.ENTERING,
    }:
        state.transition(CaseStatus.FAILED, expected=current.status, reason=reason)
    result = Result(
        status="failed", reason=reason, steps=0, elapsed_ms=(time.perf_counter() - started) * 1000
    )
    atomic_write_json(case_dir / "result.json", result)
    RunLog(case_dir, case_id=case_id, agent_id="workflow").append(
        "stop", payload={"stage": "document_workflow", "status": "failed", "reason": reason}
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True, help="Harness case identity only")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--relay-url", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--recovery-model-config", type=Path)
    parser.add_argument("--env-path", type=Path)
    parser.add_argument("--pipeline-config", type=Path, default=Path("config/pipeline.toml"))
    parser.add_argument("--extraction-model-config", type=Path)
    parser.add_argument("--summary-strategy", choices=["flat_fields", "label_grouped"])
    args = parser.parse_args(argv)
    started, owned_case, case_id = time.perf_counter(), False, None
    try:
        # Deliberately do not deserialize ShipmentRecord or inspect its fields.
        case_id = _HarnessIdentity.model_validate_json(args.record.read_bytes()).case_id
        args.case_dir.mkdir(parents=True, exist_ok=False)
        owned_case = True
        _, documents = load_inputs(args.manifest)
        settings = PipelineSettings.load(args.pipeline_config)
        brain_settings = BrainSettings.load(args.config)
        combined_limit = min(settings.max_cost_usd, brain_settings.max_cost_usd)
        if combined_limit <= 0:
            raise ValueError("workflow_has_no_model_budget")
        settings = settings.model_copy(update={"max_cost_usd": combined_limit})
        extraction_config = args.extraction_model_config or args.model_config
        preparation = prepare_case(
            args.case_dir,
            case_id=case_id,
            documents=documents,
            settings=settings,
            model_factory=lambda log: create_model_client(
                extraction_config, run_log=log, env_path=args.env_path
            ),
            reservation=ImageCallBudget.from_config(extraction_config),
        )
        atomic_write_json(
            args.case_dir / "pipeline_result.json",
            {
                "schema_version": "0.1",
                "ready": preparation.ready,
                "known_cost_usd": preparation.known_cost_usd,
                "unknown_cost": preparation.unknown_cost,
                "error_type": preparation.error_type,
                "blocking_codes": sorted(
                    {
                        check.code
                        for check in preparation.checks.checks
                        if check.severity == "blocking"
                    }
                ),
            },
        )
        if not preparation.ready or preparation.record is None or preparation.unknown_cost:
            _terminal_failure(args.case_dir, case_id, "document_review_required", started)
            print(json.dumps({"status": "failed", "reason": "document_review_required"}))
            return 1
        brain_args = [
            "--record",
            str(args.case_dir / "record.json"),
            "--case-dir",
            str(args.case_dir),
            "--prepared-case",
            "--relay-url",
            args.relay_url,
            "--goal",
            args.goal,
            "--config",
            str(args.config),
            "--model-config",
            str(args.model_config),
            "--max-cost-usd",
            str(combined_limit),
        ]
        for flag, value in (
            ("--env-path", args.env_path),
            ("--recovery-model-config", args.recovery_model_config),
            ("--summary-strategy", args.summary_strategy),
        ):
            if value is not None:
                brain_args.extend([flag, str(value)])
        # The existing prepared-case path validates READY + origin=extracted and
        # reopens this same run_log, so extraction costs count toward brain limits.
        return run_brain(brain_args)
    except Exception as exc:
        reason = "workflow_setup_error:" + type(exc).__name__
        if owned_case and case_id is not None:
            _terminal_failure(args.case_dir, case_id, reason, started)
        print(json.dumps({"status": "failed", "reason": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
