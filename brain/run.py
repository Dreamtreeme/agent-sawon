"""Run one new private case against an authenticated desktop relay."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

from sawon_interfaces import ShipmentRecord

from brain.graph import Brain, Result
from brain.relay import HttpRelay
from brain.settings import BrainSettings
from brain.task_instruction import TaskInputRecord
from models.budget import CallBudget
from models.factory import create_model_client
from pipeline.handoff import validate_prepared_case
from sawon_runtime.storage import CaseStatus, RunLog, StateStore, atomic_write_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--relay-url", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--task", type=Path, help="User-authored task description JSON")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--env-path", type=Path)
    parser.add_argument("--recovery-model-config", type=Path)
    parser.add_argument("--visual-ocr-config", type=Path)
    parser.add_argument("--prepared-case", action="store_true")
    parser.add_argument("--summary-strategy", choices=["flat_fields", "label_grouped"])
    parser.add_argument(
        "--max-cost-usd", type=float, help="Lower total case budget, including preparation calls"
    )
    args = parser.parse_args(argv)

    if args.max_cost_usd is not None and (
        not math.isfinite(args.max_cost_usd) or args.max_cost_usd < 0
    ):
        parser.error("--max-cost-usd must be finite and non-negative")

    case_created = False
    log = None
    exit_code = 2

    try:
        raw_record = json.loads(args.record.read_text(encoding="utf-8"))
        record = (
            TaskInputRecord.model_validate(raw_record)
            if "inputs" in raw_record
            else ShipmentRecord.model_validate(raw_record)
        )

        if (
            isinstance(record, ShipmentRecord)
            and record.origin == "extracted"
            and not args.prepared_case
        ):
            raise ValueError("Extracted records require a validated prepared case")

        settings = BrainSettings.load(args.config)

        if args.task is not None:
            settings = settings.model_copy(update={"task_path": args.task.resolve()})

        if args.max_cost_usd is not None:
            if args.max_cost_usd > settings.max_cost_usd:
                raise ValueError("A total case budget override may only reduce the configured cap")
            settings = settings.model_copy(update={"max_cost_usd": args.max_cost_usd})

        if args.summary_strategy:
            settings = settings.model_copy(update={"summary_strategy": args.summary_strategy})

        if args.prepared_case:
            if not isinstance(record, ShipmentRecord):
                raise ValueError("Prepared extraction requires a shipment record")
            if args.record.resolve() != (args.case_dir / "record.json").resolve():
                raise ValueError("Prepared extraction must use its own record path")
            validate_prepared_case(args.case_dir, record)
        else:
            args.case_dir.mkdir(parents=True, exist_ok=False)
            case_created = True

        log = RunLog(args.case_dir, case_id=record.case_id, agent_id="brain")

        if not args.prepared_case:
            atomic_write_json(args.case_dir / "record.json", record)

        case_created = True

        if settings.recovery_enabled and args.recovery_model_config is None:
            raise ValueError("Recovery requires an explicit model configuration")

        relay = HttpRelay(
            args.relay_url,
            os.environ.get("SAWON_RELAY_TOKEN", ""),
            timeout=settings.http_timeout_seconds,
            max_depth=settings.observation_max_depth,
            visual=settings.is_visual,
        )

        visual_ocr = None
        if settings.is_visual:
            if args.visual_ocr_config is None:
                raise ValueError("Visual mode requires --visual-ocr-config")
            from pipeline.ocr import PaddleOcrEngine
            from pipeline.schemas import PipelineSettings

            visual_ocr = PaddleOcrEngine(PipelineSettings.load(args.visual_ocr_config))

        model = create_model_client(args.model_config, run_log=log, env_path=args.env_path)
        recovery_model = (
            create_model_client(args.recovery_model_config, run_log=log, env_path=args.env_path)
            if settings.recovery_enabled
            else None
        )

        result = Brain(
            record=record,
            goal=args.goal,
            relay=relay,
            model=model,
            settings=settings,
            case_dir=args.case_dir,
            run_log=log,
            username=os.environ.get("SAWON_APP_USERNAME"),
            password=os.environ.get("SAWON_APP_PASSWORD"),
            call_budget=CallBudget.from_config(args.model_config),
            recovery_model=recovery_model,
            recovery_budget=(
                CallBudget.from_config(args.recovery_model_config)
                if settings.recovery_enabled
                else None
            ),
            prepared_case=args.prepared_case,
            visual_ocr=visual_ocr,
        ).run()

        print(result.model_dump_json())
        exit_code = 0 if result.status == "success" else 1

    except Exception as exc:
        reason = "setup_error:" + type(exc).__name__

        if case_created and not (args.case_dir / "result.json").exists():
            result = Result(status="failed", reason=reason, steps=0, elapsed_ms=0)
            atomic_write_json(args.case_dir / "result.json", result)
            state = StateStore(args.case_dir, case_id=record.case_id)
            current = state.initialize()
            if current.status in {CaseStatus.RECEIVED, CaseStatus.READY, CaseStatus.ENTERING}:
                state.transition(CaseStatus.FAILED, expected=current.status, reason=reason)
            if log is not None:
                log.append("stop", payload={"status": "failed", "reason": reason})

        print(json.dumps({"status": "failed", "reason": reason}))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
