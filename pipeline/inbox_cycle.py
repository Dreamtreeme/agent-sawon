"""Run successive inbox and ERP turns on one PC."""

import argparse
import json
import math
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from uuid import uuid4

import portalocker

from pipeline.inbox import (
    add_live_arguments,
    add_planner_arguments,
    collect_live,
    desktop_turn,
    plan_next,
    run_worker,
)


def run_cycles(
    args: argparse.Namespace,
    *,
    cycles: int,
    interval_seconds: float,
    receive: Callable[[sqlite3.Connection], dict],
    dispatch: Callable[[int], int],
) -> Iterator[dict]:
    """Collect and plan under desktop_turn; dispatch owns its normal worker turn."""
    if cycles < 1 or not math.isfinite(interval_seconds) or interval_seconds < 0:
        raise ValueError("Provide a positive cycle count and a finite, nonnegative interval")
    args.state.mkdir(parents=True, exist_ok=True)
    # Keep two cycle runners from picking the same plan in the handoff to the worker.
    with portalocker.Lock(args.state / "cycle.lock", mode="a", timeout=0):
        for cycle in range(1, cycles + 1):
            if cycle > 1:
                time.sleep(interval_seconds)
            with desktop_turn(args.state) as db:
                collection = receive(db)
                decision = plan_next(db, args)
                plan = decision["plan_id"]
                if decision["status"] == "empty":
                    # The prior run may have stopped after committing a plan, before dispatch.
                    queued = db.execute(
                        "SELECT id FROM plans WHERE stage='planned' ORDER BY id LIMIT 1"
                    ).fetchone()
                    plan = queued["id"] if queued else None
            code = dispatch(plan) if plan is not None else None
            yield {
                "cycle": cycle,
                "collection": collection,
                "decision": decision,
                "plan_id": plan,
                "worker_exit": code,
            }
            if code not in (None, 0):
                return


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Connector stdin uses a fresh compact Gmail JSON array on each line, one per cycle.",
    )
    add_live_arguments(parser)
    add_planner_arguments(parser)
    parser.add_argument("--state", type=Path, required=True, help="Private directory for this PC")
    parser.add_argument("--cycles", type=int, required=True)
    parser.add_argument("--interval-seconds", type=float, default=0, help="Pause between PC turns")
    parser.add_argument("--task", type=Path, required=True, help="Task education for new work")
    parser.add_argument("--after-dispatch-task", type=Path, help="Task education for revisions")
    parser.add_argument("--config", type=Path, required=True, help="GUI worker configuration")
    parser.add_argument("--erp-relay-url", help="ERP relay URL; defaults to --relay-url")
    args = parser.parse_args()

    def receive(db: sqlite3.Connection) -> dict:
        # The connector host provides one fresh JSON array per line and per collection.
        packet = StringIO(sys.stdin.readline()) if args.gmail_raw_stdin else None
        return collect_live(db, args, gmail_stream=packet)

    def dispatch(plan: int) -> int:
        case_dir = args.state / "cases" / f"plan-{plan}-{uuid4().hex}"
        command = [
            "--case-dir",
            str(case_dir),
            "--task",
            str(args.task),
            "--relay-url",
            args.erp_relay_url or args.relay_url,
            "--config",
            str(args.config),
            "--model-config",
            str(args.model_config),
            "--visual-ocr-config",
            str(args.ocr_config),
            "--max-cost-usd",
            str(args.max_cost_usd),
        ]
        if args.env_path is not None:
            command += ["--env-path", str(args.env_path)]
        with desktop_turn(args.state) as db, redirect_stdout(sys.stderr):
            return run_worker(
                db,
                plan,
                command,
                input_dir=args.state / "worker-inputs",
                after_dispatch_task=args.after_dispatch_task,
            )

    for result in run_cycles(
        args,
        cycles=args.cycles,
        interval_seconds=args.interval_seconds,
        receive=receive,
        dispatch=dispatch,
    ):
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if result["worker_exit"]:
            return result["worker_exit"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
