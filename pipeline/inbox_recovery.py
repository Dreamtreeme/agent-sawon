"""Recover the effect of an interrupted dispatch using a new GUI lookup."""

import json
import re
import sqlite3
from pathlib import Path
from uuid import uuid4

from brain.input_binding import resolve_task_inputs
from brain.observe.values import field_matches
from brain.task_instruction import TaskInputRecord, TaskInstruction
from sawon_runtime.storage import atomic_write_json


def recover_started(
    db: sqlite3.Connection,
    plan: int,
    *,
    state: Path,
    lookup_task: Path,
    worker_options: list[str],
) -> dict:
    from brain.run import main as run_case

    row = db.execute(
        """SELECT decision,worker_args FROM inbox_plan_state
           WHERE id=? AND effective_stage='started'""",
        (plan,),
    ).fetchone()
    if row is None:
        raise ValueError("Recovery requires an interrupted plan")
    # Recover from the frozen dispatch, including cases created before this feature.
    try:
        previous = json.loads(row["worker_args"] or "[]")
        if not isinstance(previous, list):
            raise ValueError("Expected recorded command arguments")
        old_case = Path(previous[previous.index("--case-dir") + 1])
        original = TaskInstruction.model_validate_json(
            (old_case / "task-instruction.json").read_bytes()
        )
        record = TaskInputRecord.model_validate_json((old_case / "record.json").read_bytes())
    except (IndexError, TypeError, ValueError, OSError):
        return {"status": "unconfirmed", "plan_id": plan, "reason": "original_case_missing"}
    lookup = TaskInstruction.model_validate_json(lookup_task.read_bytes())
    key = original.identity_input_key
    if (
        not key
        or key != lookup.identity_input_key
        or not record.inputs.get(key)
        or record.provenance is None
        or record.provenance.plan_id != plan
        or record.provenance.work_id != json.loads(row["decision"]).get("work_id")
    ):
        return {"status": "unconfirmed", "plan_id": plan, "reason": "identity_unavailable"}

    expected = resolve_task_inputs(record, original, {})
    if expected.keys() != record.inputs.keys():
        return {"status": "unconfirmed", "plan_id": plan, "reason": "original_inputs_unavailable"}
    attempt = state / "reconciliation" / f"plan-{plan}-{uuid4().hex}"
    attempt.mkdir(parents=True)
    case = attempt / "lookup"
    fields = {f"read.{k}": record.provenance.inputs[k].memo_label or k for k in expected}
    task = lookup.model_copy(
        update={
            "result_keys": ["matching_rows", "saved_number", *fields],
            "requirements": [
                *lookup.requirements,
                f"상세 화면에서 읽을 항목: {json.dumps(fields, ensure_ascii=False)}",
            ],
        }
    )
    atomic_write_json(attempt / "task.json", task)
    atomic_write_json(
        attempt / "query.json",
        TaskInputRecord(case_id=attempt.name, inputs={key: record.inputs[key]}),
    )
    code = run_case(
        [
            *worker_options,
            "--case-dir",
            str(case),
            "--record",
            str(attempt / "query.json"),
            "--task",
            str(attempt / "task.json"),
            "--goal",
            lookup.objective,
        ]
    )
    result = {"status": "unconfirmed", "plan_id": plan, "case_dir": str(case), "worker_exit": code}
    path = case / "task-result.json"
    if code == 0 and path.exists():
        evidence = json.loads(path.read_bytes())
        values = evidence.get("values", {})
        mismatches = [
            k for k in expected if not field_matches(k, values.get(f"read.{k}"), expected)
        ]
        result["mismatches"] = mismatches
        # A count label may include its caption and unit, such as "조회 결과1건".
        # A range, page indicator with another number, or a signed quantity is ambiguous.
        count = re.fullmatch(r"[^\d.+−-]*(\d+)[^\d.+−-]*", values.get("matching_rows", ""))
        number = values.get("saved_number", "").strip()
        if (
            evidence.get("status") == "success"
            and not mismatches
            and count
            and int(count[1]) == 1
            and number.isdecimal()
            and all(evidence.get("provenance", {}).get(k) for k in task.result_keys)
        ):
            with db:
                db.execute(
                    "INSERT INTO inbox_reconciliations VALUES(?,?,?)",
                    (plan, str(case.resolve()), number),
                )
            result.update(status="reconciled", saved_number=number)
    atomic_write_json(attempt / "outcome.json", result)
    return result
