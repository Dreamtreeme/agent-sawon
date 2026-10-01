"""Run one new private case against an authenticated desktop relay."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

from sawon_interfaces import ShipmentRecord

from brain.cache.experiment import CacheExperiment, CacheOutcome, validate_prepared_case
from brain.graph import Brain, Result
from brain.relay import HttpRelay
from brain.runtime.storage import CaseStatus, RunLog, StateStore, atomic_write_json
from brain.settings import BrainSettings
from models.budget import CallBudget
from models.factory import create_model_client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--relay-url", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--procedure", type=Path, help="User-supplied ordered workflow JSON")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument(
        "--matching-model-config",
        type=Path,
        help="Optional Jev Choice model for OCR screen matching",
    )
    parser.add_argument("--env-path", type=Path)
    parser.add_argument("--recovery-model-config", type=Path)
    parser.add_argument("--visual-ocr-config", type=Path)
    parser.add_argument("--cache-path", type=Path, help="Explicit isolated notebook cache")
    parser.add_argument("--prepared-case", action="store_true")
    parser.add_argument("--summary-strategy", choices=["flat_fields", "label_grouped"])
    parser.add_argument(
        "--max-cost-usd", type=float, help="Lower total case budget, including preparation calls"
    )
    parser.add_argument(
        "--cache-isolation",
        choices=["cold", "snapshot"],
        help="Use a fresh isolated cache for a new or validated prepared case",
    )
    parser.add_argument(
        "--cache-snapshot", type=Path, help="Immutable input for snapshot isolation"
    )
    args = parser.parse_args(argv)
    if args.cache_path is not None and args.cache_isolation is not None:
        parser.error("--cache-path cannot be combined with --cache-isolation")
    if (args.cache_isolation == "snapshot") != (args.cache_snapshot is not None):
        parser.error("--cache-snapshot is required only with --cache-isolation snapshot")
    if args.max_cost_usd is not None and (
        not math.isfinite(args.max_cost_usd) or args.max_cost_usd < 0
    ):
        parser.error("--max-cost-usd must be finite and non-negative")
    case_created = False
    log = None
    experiment = None
    outcome: CacheOutcome = "interrupted"
    error_type = None
    exit_code = 2
    try:
        record = ShipmentRecord.model_validate_json(args.record.read_bytes())
        if record.origin == "extracted" and not args.prepared_case:
            raise ValueError("Extracted records require a validated prepared case")
        settings = BrainSettings.load(args.config)
        if args.procedure is not None:
            settings = settings.model_copy(update={"procedure_path": args.procedure.resolve()})
        if args.cache_path is not None:
            if not settings.cache.enabled:
                raise ValueError("A cache path requires cache-enabled configuration")
            settings = settings.model_copy(
                update={
                    "cache": settings.cache.model_copy(update={"path": args.cache_path.resolve()})
                }
            )
        if args.max_cost_usd is not None:
            if args.max_cost_usd > settings.max_cost_usd:
                raise ValueError("A total case budget override may only reduce the configured cap")
            settings = settings.model_copy(update={"max_cost_usd": args.max_cost_usd})
        if args.summary_strategy:
            settings = settings.model_copy(update={"summary_strategy": args.summary_strategy})
        if args.cache_isolation:
            experiment = CacheExperiment(
                settings,
                case_dir=args.case_dir,
                mode=args.cache_isolation,
                snapshot=args.cache_snapshot,
            )
        # mkdir(exist_ok=False) makes retries explicitly choose a fresh case directory.
        if args.prepared_case:
            if args.record.resolve() != (args.case_dir / "record.json").resolve():
                raise ValueError("Prepared extraction must use its own record path")
            validate_prepared_case(args.case_dir, record)
        else:
            args.case_dir.mkdir(parents=True, exist_ok=False)
            case_created = True
        log = RunLog(args.case_dir, case_id=record.case_id, agent_id="brain")
        if not args.prepared_case:
            atomic_write_json(args.case_dir / "record.json", record)
        if experiment is not None:
            settings = experiment.start(prepared_record=record if args.prepared_case else None)
        case_created = True
        if settings.recovery_enabled and args.recovery_model_config is None:
            raise ValueError("Recovery requires an explicit model configuration")
        relay = HttpRelay(
            args.relay_url,
            os.environ.get("SAWON_RELAY_TOKEN", ""),
            timeout=settings.http_timeout_seconds,
            max_depth=settings.observation_max_depth,
            visual=settings.perception_mode == "visual_only",
        )
        visual_ocr = None
        if settings.perception_mode == "visual_only":
            if args.visual_ocr_config is None:
                raise ValueError("Visual mode requires --visual-ocr-config")
            from pipeline.ocr import PaddleOcrEngine
            from pipeline.schemas import PipelineSettings

            visual_ocr = PaddleOcrEngine(PipelineSettings.load(args.visual_ocr_config))
        model = create_model_client(args.model_config, run_log=log, env_path=args.env_path)
        matching_model = (
            create_model_client(args.matching_model_config, run_log=log, env_path=args.env_path)
            if args.matching_model_config
            else None
        )
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
            matching_model=matching_model,
            matching_budget=(
                CallBudget.from_config(args.matching_model_config)
                if args.matching_model_config
                else None
            ),
        ).run()
        outcome = result.status
        print(result.model_dump_json())
        exit_code = 0 if result.status == "success" else 1
    except Exception as exc:
        # Do not print provider exception bodies, bearer token, or password.
        reason = "setup_error:" + type(exc).__name__
        outcome, error_type = "failed", type(exc).__name__
        owned_case = case_created or (experiment is not None and experiment.started)
        if owned_case and not (args.case_dir / "result.json").exists():
            result = Result(status="failed", reason=reason, steps=0, elapsed_ms=0)
            atomic_write_json(args.case_dir / "result.json", result)
            state = StateStore(args.case_dir, case_id=record.case_id)
            current = state.initialize()
            if current.status in {CaseStatus.RECEIVED, CaseStatus.READY, CaseStatus.ENTERING}:
                state.transition(CaseStatus.FAILED, expected=current.status, reason=reason)
            if log is not None:
                log.append("stop", payload={"status": "failed", "reason": reason})
        print(json.dumps({"status": "failed", "reason": reason}))
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        if experiment is not None and experiment.started:
            try:
                if not experiment.finish(outcome=outcome, error_type=error_type):
                    exit_code = 2
            except Exception as exc:
                # Cache evidence failure must not overwrite a business result or log.
                print(json.dumps({"cache_experiment_error": type(exc).__name__}))
                exit_code = 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
