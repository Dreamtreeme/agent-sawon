import json
from argparse import Namespace
from pathlib import Path

import pytest

from brain.run import worker_parser
from pipeline.inbox import collect, desktop_turn, plan_next, record_plan
from pipeline.inbox_recovery import recover_started
from pipeline.inbox_sources import read_mail_bytes
from sawon_runtime.storage import atomic_write_json


@pytest.mark.parametrize("fault", [None, "duplicate", "old_quantity", "missing_evidence"])
def test_reconcile_interruption_from_fresh_lookup_without_replaying_save(
    tmp_path, monkeypatch, fault
):
    """Synthetic original dispatch and GUI observations exercise the real persistence boundary."""
    case = tmp_path / "interrupted"
    case.mkdir()
    task = Path("examples/shipment-quantity-correction-task.json")
    (case / "task-instruction.json").write_bytes(task.read_bytes())
    fields = {"hbl_no": "TEST-A", "packages.count": "18"}
    atomic_write_json(
        case / "record.json",
        {
            "case_id": "old",
            "inputs": fields,
            "origin": "inbox_plan",
            "provenance": {
                "plan_id": 1,
                "work_id": "A",
                "inputs": {k: {"receipt_id": 1, "source": "email", "fact_key": k} for k in fields},
            },
        },
    )
    previous = [
        "--case-dir",
        str(case),
        "--record",
        "old.json",
        "--config",
        "old.toml",
        "--relay-url",
        "http://old",
        "--model-config",
        "model.toml",
        "--goal",
        "write",
    ]

    def lookup(argv):
        opts = worker_parser().parse_args(argv)
        assert json.loads(opts.record.read_bytes())["inputs"] == {"hbl_no": "TEST-A"}
        assert opts.case_dir != case and opts.relay_url != "http://old"
        opts.case_dir.mkdir()
        values = {
            "read.hbl_no": "TEST-A",
            "read.packages.count": "18",
            "saved_number": "3",
            "matching_rows": "조회 결과1건",
        }
        if fault == "duplicate":
            values["matching_rows"] = "2"
        if fault == "old_quantity":
            values["read.packages.count"] = "12"
        provenance = {k: [{"observation_id": "new", "value": v}] for k, v in values.items()}
        if fault == "missing_evidence":
            del provenance["saved_number"]
        atomic_write_json(
            opts.case_dir / "task-result.json",
            {
                "status": "success",
                "values": values,
                "provenance": provenance,
            },
        )
        return 0

    monkeypatch.setattr("brain.run.main", lookup)
    with desktop_turn(tmp_path) as db:
        first = collect(db, [read_mail_bytes(b"Subject: request\r\n\r\nA", "desk")])
        plan = record_plan(db, first, {"work_id": "A", "goal": "write"})
        with db:
            db.execute(
                "UPDATE plans SET stage='started',worker_args=? WHERE id=?",
                (json.dumps(previous), plan),
            )
        assert plan_next(db, Namespace())["status"] == "recovery_required"
        outcome = recover_started(
            db,
            plan,
            state=tmp_path,
            lookup_task=Path("examples/shipment-save-lookup-task.json"),
            worker_options=[
                "--relay-url",
                "http://current",
                "--config",
                "test.toml",
                "--model-config",
                "model.toml",
            ],
        )
        state = tuple(
            db.execute("SELECT stage,worker_exit,effective_stage FROM inbox_plan_state").fetchone()
        )
        assert state == ("started", None, "started" if fault else "reconciled")
        assert outcome["status"] == ("unconfirmed" if fault else "reconciled")
        assert plan_next(db, Namespace())["status"] == ("recovery_required" if fault else "empty")
