import argparse
import base64
import io
import json
import sys

import portalocker
import pytest
from test_inbox_planning import planner as planner
from test_inbox_planning import work

from brain.run import worker_parser
from pipeline.inbox import collect, desktop_turn, record_plan, run_worker
from pipeline.inbox_cycle import main, run_cycles
from pipeline.inbox_sources import read_mail_bytes
from sawon_runtime.storage import atomic_write_json


def test_one_start_collects_again_after_registration_and_amends_once(planner, monkeypatch, capsys):
    """Synthetic Gmail, Kakao, model replies and ERP; real source, plan and worker handoffs."""
    state, replies, prompts, _ = planner
    root = state.parent
    task, edit = root / "register.json", root / "edit.json"
    for path in (task, edit):
        atomic_write_json(
            path,
            {
                "name": path.stem,
                "objective": "Save order",
                "identity_input_key": "hbl_no",
                "completion_conditions": ["Record saved"],
                "procedure": [
                    {
                        "id": "quantity",
                        "instruction": "Enter quantity",
                        "completion": "Quantity saved",
                        "actions": [
                            {"verb": "fill", "target": "Packages", "input_key": "packages"}
                        ],
                    }
                ],
            },
        )
    (root / "ocr.toml").write_text("", encoding="utf-8")
    mail = b"Subject: Order\r\nMessage-ID: <new@local.test>\r\n\r\nTEST456: 18 CTNS"
    packet = json.dumps([{"id": "new", "raw": base64.urlsafe_b64encode(mail).decode("ascii")}])
    # On the second scan the mail is absent. Its original identity must still reach the edit.
    stream = io.StringIO("\n".join([packet, "[]", packet]))
    monkeypatch.setattr(sys, "stdin", stream)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "inbox_cycle",
            "--state",
            str(state),
            "--cycles",
            "3",
            "--gmail-raw-stdin",
            "--mailbox",
            "desk",
            "--mail-subject",
            "Order",
            "--room",
            "test",
            "--room-title",
            "시험방",
            "--chat-marker",
            "TEST456",
            "--relay-url",
            "http://localhost:1",
            "--erp-relay-url",
            "http://localhost:2",
            "--ocr-config",
            str(root / "ocr.toml"),
            "--config",
            "config/brain.visual.toml",
            "--task",
            str(task),
            "--after-dispatch-task",
            str(edit),
        ],
    )
    monkeypatch.setenv("SAWON_RELAY_TOKEN", "synthetic")
    monkeypatch.setattr("brain.relay.HttpRelay.health", lambda self: {})
    ledger = {}
    dispatched = []

    def fake_chat(relay, room, destination, **_):
        assert relay.url == "http://localhost:1"
        text = (
            f"{room} 님과 카카오톡 대화\n--------------- 2026년 11월 20일 금요일 ---------------\n"
        )
        if dispatched:
            text += "[시험] [오전 11:50] TEST456: correct to 22 CTNS\n"
        destination.write_text(text, encoding="utf-8")

    def fake_erp(argv):
        options = worker_parser().parse_args(argv)
        assert options.relay_url == "http://localhost:2"
        record = json.loads(options.record.read_bytes())
        key, quantity = record["inputs"]["hbl_no"], record["inputs"]["packages"]
        assert (key in ledger) == (options.task == edit)
        ledger[key] = quantity
        dispatched.append(
            (options.task, quantity, record["provenance"]["inputs"]["packages"]["source"])
        )
        return 0

    monkeypatch.setattr("pipeline.kakao_inbox.export_kakao_chat", fake_chat)
    monkeypatch.setattr("brain.run.main", fake_erp)
    create = work()
    create.update(receipt_ids=[1], goal="Register TEST456, 18 CTNS")
    count = create["facts"][1]
    count.update(value="18 CTNS", receipt_id=1)
    replies.extend(
        [
            create,
            {
                **create,
                "receipt_ids": [2],
                "facts": [{**count, "value": "22 CTNS", "receipt_id": 2}],
                "goal": "Update existing TEST456 to 22 CTNS",
                "supersedes_plan_id": 1,
            },
        ]
    )

    assert main() == 0
    turns = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["collection"]["gmail"]["read"] for r in turns] == [1, 0, 1]
    assert [r["collection"]["kakao"]["new_receipt_ids"] for r in turns] == [[], [2], []]
    assert [r["decision"]["model_calls"] for r in turns] == [1, 1, 0]
    assert [r["worker_exit"] for r in turns] == [0, 0, None]
    assert dispatched == [(task, "18 CTNS", "email"), (edit, "22 CTNS", "kakao_message")]
    assert ledger == {"TEST456": "22 CTNS"} and len(prompts) == 2

    # Connector EOF is a failed read, not a silently successful empty check.
    with pytest.raises(json.JSONDecodeError):
        main()
    assert len(prompts) == len(dispatched) == 2


def test_restart_dispatches_only_latest_unstarted_plan_and_excludes_competing_runner(
    tmp_path, monkeypatch
):
    args = argparse.Namespace(state=tmp_path)
    mail = read_mail_bytes(b"Subject: Register\r\n\r\nA", "desk")
    change = read_mail_bytes(b"Subject: Correction\r\n\r\nB", "desk")
    with desktop_turn(tmp_path) as db:
        previous = record_plan(db, collect(db, [mail]), {"goal": "Register A"})
        latest = record_plan(
            db,
            collect(db, [change]),
            {
                "goal": "Register B",
                "supersedes_plan_id": previous,
            },
        )

    def receive(db):
        return {"new_receipt_ids": collect(db, [mail, change])}

    attempts = []

    def dispatch(plan):
        with pytest.raises(portalocker.exceptions.LockException):
            list(
                run_cycles(
                    args, cycles=1, interval_seconds=0, receive=receive, dispatch=pytest.fail
                )
            )
        with desktop_turn(tmp_path) as db:
            attempts.append(plan)
            return run_worker(db, plan, [])

    monkeypatch.setattr("brain.run.main", lambda _: 1)
    (failed,) = run_cycles(args, cycles=3, interval_seconds=0, receive=receive, dispatch=dispatch)
    assert (failed["plan_id"], failed["worker_exit"]) == (latest, 1)
    (restart,) = run_cycles(args, cycles=1, interval_seconds=0, receive=receive, dispatch=dispatch)
    assert restart["collection"]["new_receipt_ids"] == [] and restart["plan_id"] is None
    assert attempts == [latest]
