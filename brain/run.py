"""Run one new private case against an authenticated desktop relay."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sawon_interfaces import ShipmentRecord

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
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--env-path", type=Path)
    parser.add_argument("--summary-strategy", choices=["flat_fields", "label_grouped"])
    args = parser.parse_args(argv)
    case_created = False
    log = None
    try:
        record = ShipmentRecord.model_validate_json(args.record.read_bytes())
        settings = BrainSettings.load(args.config)
        if args.summary_strategy:
            settings = settings.model_copy(update={"summary_strategy": args.summary_strategy})
        relay = HttpRelay(
            args.relay_url,
            os.environ.get("SAWON_RELAY_TOKEN", ""),
            timeout=settings.http_timeout_seconds,
            max_depth=settings.observation_max_depth,
        )
        # mkdir(exist_ok=False) makes retries explicitly choose a fresh case directory.
        args.case_dir.mkdir(parents=True, exist_ok=False)
        case_created = True
        log = RunLog(args.case_dir, case_id=record.case_id, agent_id="brain")
        atomic_write_json(args.case_dir / "record.json", record)
        model = create_model_client(args.model_config, run_log=log, env_path=args.env_path)
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
        ).run()
        print(result.model_dump_json())
        return 0 if result.status == "success" else 1
    except Exception as exc:
        # Do not print provider exception bodies, bearer token, or password.
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
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
