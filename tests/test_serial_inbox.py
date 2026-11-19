import argparse
import json
import sys
from decimal import Decimal
from unittest.mock import Mock

import portalocker
import pytest
from test_brain import ScriptedModel

from brain import run
from brain.graph.screen_memory import screen_input_values
from brain.observe.values import field_matches
from brain.task_instruction import TaskInputRecord, TaskInstruction
from pipeline.inbox import collect, desktop_turn, main, pending, record_plan, run_worker
from pipeline.inbox_sources import SourceItem, read_chat, read_mail
from sawon_runtime.storage import atomic_write_json


def test_mail_and_chat_correction_survive_restart_and_plan_together(tmp_path):
    state = tmp_path / "state"
    mail = tmp_path / "order.eml"
    mail.write_bytes(b"Message-ID: <order-a@example.com>\r\nSubject: Order A\r\n\r\n24 boxes")
    chat = tmp_path / "room.txt"
    chat.write_text("시험방 님과 카카오톡 대화\n[담당자] A를 30박스로 정정", encoding="utf-8")
    sources = [read_mail(mail, "purchasing"), read_chat(chat, "시험방")]
    with desktop_turn(state) as db:
        receipt_ids = collect(db, sources)
        assert len(receipt_ids) == 2

    with desktop_turn(state) as db:
        mail_row, chat_row = pending(db)
        assert mail_row["source"] == "email" and "24 boxes" in mail_row["new_text"]
        assert chat_row["kind"] == "initial" and "30박스" in chat_row["new_text"]
        assert collect(db, sources) == []
        with pytest.raises(ValueError):
            record_plan(db, [mail_row["id"], max(receipt_ids) + 1], {"goal": "잘못된 연결"})
        assert [row["id"] for row in pending(db)] == receipt_ids
        record_plan(db, receipt_ids, {"goal": "A의 수량을 30박스로 정정"})

    with desktop_turn(state) as db:
        assert collect(db, sources) == [] and pending(db) == []
        assert [row["raw"] for row in db.execute("SELECT raw FROM receipts ORDER BY id")] == [
            mail.read_bytes(),
            chat.read_bytes(),
        ]
        assert db.execute("SELECT count(*) FROM plans").fetchone()[0] == 1


def test_baseline_only_skips_first_snapshot_and_repeated_lines_remain_distinct(tmp_path):
    path = tmp_path / "room.txt"
    first = "시험방 님과 카카오톡 대화\n[담당자] 확인"
    same_message = "[담당자] 확인"
    with desktop_turn(tmp_path / "state") as db:
        path.write_text(first, encoding="utf-8")
        collect(db, [read_chat(path, "시험방")], baseline_chat=True)
        assert pending(db) == []
        path.write_text(first + "\n" + same_message + "\n" + same_message, encoding="utf-8")
        new_ids = collect(db, [read_chat(path, "시험방")], baseline_chat=True)
        (append,) = pending(db)
        assert append["kind"] == "append" and append["new_text"].splitlines() == [same_message] * 2
        record_plan(db, new_ids, {"goal": "새 연락 두 건 확인"})
        path.write_text("시험방 님과 카카오톡 대화\n[담당자] 정정", encoding="utf-8")
        collect(db, [read_chat(path, "시험방")])
        (changed,) = pending(db)
        assert changed["kind"] == "review" and "정정" in changed["new_text"]


def test_dispatch_holds_desktop_and_keeps_failed_exit_without_replaying(tmp_path, monkeypatch):
    state = tmp_path / "state"
    mail = tmp_path / "letter.eml"
    mail.write_bytes(b"Subject: Job\r\n\r\nNew order")
    goal = "등록번호로 선적 A 재조회"
    calls = []
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal", required=True)
    parser.add_argument("--case-dir", required=True)

    def fake_main(arguments):
        calls.append(list(arguments))
        with (
            pytest.raises(portalocker.exceptions.LockException),
            portalocker.Lock(state / "desktop.lock", timeout=0),
        ):
            pytest.fail("another process could start a desktop turn during execution")
        return 1

    monkeypatch.setattr(run, "main", fake_main)
    with desktop_turn(state) as db:
        ids = collect(db, [read_mail(mail, "orders")])
        plan = record_plan(db, ids, {"goal": goal})
        assert run_worker(db, plan, ["--case-dir", "trial-1"]) == 1

    with desktop_turn(state) as db:
        saved = db.execute("SELECT stage,worker_exit,worker_args FROM plans WHERE id=?", (plan,))
        stage, code, args_json = saved.fetchone()
        assert (stage, code) == ("exited", 1)
        assert [json.loads(args_json)] == calls
        with pytest.raises(ValueError):
            run_worker(db, plan, ["--case-dir", "trial-2"])
    (arguments,) = calls
    assert vars(parser.parse_args(arguments)) == {"goal": goal, "case_dir": "trial-1"}


def test_correction_can_replace_an_unstarted_plan_and_preserves_original(tmp_path):
    with desktop_turn(tmp_path / "desk") as db:
        eml = tmp_path / "source.eml"
        eml.write_bytes(b"Subject: order\r\n\r\n24")
        first = record_plan(db, collect(db, [read_mail(eml, "desk")]), {"goal": "24 boxes"})
        eml.write_bytes(b"Subject: correction\r\n\r\n30")
        later = {"goal": "30 boxes", "supersedes_plan_id": first}
        correction = collect(db, [read_mail(eml, "desk")])
        with pytest.raises(ValueError, match="Receipt"):
            record_plan(db, [*correction, 999], later)
        second = record_plan(db, correction, later)
        with pytest.raises(ValueError):
            run_worker(db, first, [])
        assert [(r["id"], r["stage"]) for r in db.execute("SELECT * FROM plans ORDER BY id")] == [
            (first, "superseded"),
            (second, "planned"),
        ]


def test_worker_from_revised_plan_binds_sources_before_dispatch_and_deducts_cost(
    tmp_path, monkeypatch
):
    state, case, task = tmp_path / "state", tmp_path / "case", tmp_path / "task.json"
    atomic_write_json(
        task,
        {
            "name": "Register a shipment",
            "objective": "Save and reopen the order",
            "identity_input_key": "reference",
            "completion_conditions": ["Saved order matches"],
            "procedure": [
                {
                    "id": "entry",
                    "instruction": "Enter quantity",
                    "completion": "Count entered",
                    "actions": [
                        {"verb": "fill", "target": "Quantity", "input_key": "count"},
                        {"verb": "fill", "target": "Package unit", "input_key": "unit"},
                    ],
                }
            ],
        },
    )
    amendment = tmp_path / "amend.json"
    edit_taught = json.loads(task.read_bytes())
    edit_taught["name"] = "Edit an existing shipment"
    edit_taught["procedure"][0]["actions"] = [
        {"verb": "fill", "target": "Quantity", "input_key": "box_total"}
    ]
    edit_taught["input_formats"] = {"box_total": {"type": "integer", "count_unit_from": "unit"}}
    atomic_write_json(amendment, edit_taught)
    with desktop_turn(state) as db:
        (mail,) = collect(db, [SourceItem("email", "desk", "m", "Ship A-17", b"Ship A-17")])
        identity = {"key": "reference", "value": "A-17", "receipt_id": mail}
        initial = {"work_id": "A", "goal": "Register A", "facts": [identity]}
        first = record_plan(db, [mail], initial)
        (chat,) = collect(db, [SourceItem("kakao_message", "room", "c", "32 boxes", b"32 boxes")])
        count = {"key": "box_total", "value": "32", "receipt_id": chat}
        unit = {"key": "package_kind", "value": "CTNS", "receipt_id": mail}
        revised = {**initial, "supersedes_plan_id": first, "facts": [identity, count, unit]}
        plan = record_plan(db, [chat], revised)

    def model_factory(_config, *, run_log, env_path):
        reply = {
            "bindings": [
                {"input_key": "count", "fact_key": "box_total"},
                {"input_key": "unit", "fact_key": "package_kind"},
            ]
        }
        return ScriptedModel(run_log, [reply], price=Decimal("0.03"))

    worker = Mock()
    worker.return_value.run.return_value = run.Result(
        status="success",
        reason="test",
        steps=0,
        elapsed_ms=0,
    )
    monkeypatch.setattr("pipeline.inbox_binding.create_model_client", model_factory)
    monkeypatch.setattr(run, "create_model_client", model_factory)
    monkeypatch.setattr(run, "Brain", worker)
    monkeypatch.setenv("SAWON_RELAY_TOKEN", "local-test")
    command = [
        "pipeline.inbox",
        "--state",
        str(state),
        "worker",
        "--from-plan",
        "--after-dispatch-task",
        str(amendment),
        "--plan",
        str(plan),
        "--",
        f"--task={task}",
        f"--case-dir={case}",
        "--relay-url=http://127.0.0.1",
        "--config=config/brain.visual.toml",
        "--model-config=config/models.toml",
        "--visual-ocr-config=config/pipeline.toml",
    ]
    monkeypatch.setattr(sys, "argv", [*command, "--max-cost-usd=0"])
    with pytest.raises(ValueError, match="cost cap"):
        main()
    with desktop_turn(state) as db:
        row = db.execute("SELECT stage,worker_args FROM plans WHERE id=?", (plan,)).fetchone()
        assert tuple(row) == ("planned", None)
    worker.assert_not_called()
    assert not case.exists()

    monkeypatch.setattr(sys, "argv", [*command, "--max-cost-usd=0.50"])
    assert main() == 0
    worker.assert_called_once()
    dispatched = worker.call_args.kwargs
    saved = TaskInputRecord.model_validate_json((case / "record.json").read_bytes())
    assert saved == dispatched["record"]
    assert saved.inputs == {"reference": "A-17", "count": "32", "unit": "CTNS"}
    assert dispatched["settings"].task_path == task
    assert dispatched["settings"].max_cost_usd == 0.47 and dispatched["goal"] == revised["goal"]
    assert saved.provenance.plan_id == plan and saved.provenance.work_id == "A"
    assert {
        key: (source.fact_key, source.receipt_id, source.source)
        for key, source in saved.provenance.inputs.items()
    } == {
        "reference": ("reference", mail, "email"),
        "count": ("box_total", chat, "kakao_message"),
        "unit": ("package_kind", mail, "email"),
    }

    def correct(db, previous, quantity):
        (receipt,) = collect(
            db, [SourceItem("kakao_message", "room", quantity, quantity, quantity.encode())]
        )
        decision = {
            **revised,
            "supersedes_plan_id": previous,
            "facts": [
                identity,
                {**count, "value": quantity, "receipt_id": receipt},
                {**unit, "value": "PLT", "receipt_id": receipt},
            ],
        }
        return record_plan(db, [receipt], decision), receipt

    with desktop_turn(state) as db:
        row = db.execute("SELECT stage,worker_exit FROM plans WHERE id=?", (plan,)).fetchone()
        assert tuple(row) == ("exited", 0)
        previous = plan
        for quantity in ("41", "45 CTNS"):
            previous, receipt = correct(db, previous, quantity)
    # Two corrections before dispatch still edit the successfully registered ancestor.
    next_case = tmp_path / "amended-case"
    command[command.index("--plan") + 1] = str(previous)
    command[command.index(f"--case-dir={case}")] = f"--case-dir={next_case}"
    monkeypatch.setattr(sys, "argv", [*command, "--max-cost-usd=0"])
    assert main() == 0
    amended = worker.call_args.kwargs
    assert amended["settings"].task_path == amendment
    assert amended["record"].inputs == {"box_total": "45 CTNS", "reference": "A-17"}
    assert amended["record"].provenance.inputs["reference"].receipt_id == mail
    assert amended["record"].provenance.inputs["box_total"].receipt_id == receipt
    reference = amended["record"].provenance.inputs["box_total"].unit_reference
    assert (reference.value, reference.fact_key, reference.plan_id, reference.receipt_id) == (
        "CTNS",
        "package_kind",
        plan,
        mail,
    )
    inputs = screen_input_values(amended["record"], TaskInstruction.model_validate(edit_taught), {})
    preview = screen_input_values(inputs, TaskInstruction.model_validate(edit_taught), {})
    assert field_matches("box_total", "45 cartons", preview)
    assert not field_matches("box_total", "45 PLT", preview)

    # The successful quantity-only amendment carried PLT in its decision and wrote no unit.
    # Both following amendments must still use the unit actually dispatched in the original record.
    for quantity in ("23 PLT", "23 CTNS"):
        with desktop_turn(state) as db:
            previous, _ = correct(db, previous, quantity)
        command[command.index("--plan") + 1] = str(previous)
        command[command.index(f"--case-dir={next_case}")] = f"--case-dir={tmp_path / quantity}"
        next_case = tmp_path / quantity
        monkeypatch.setattr(sys, "argv", [*command, "--max-cost-usd=0"])
        if quantity == "23 PLT":
            with pytest.raises(ValueError, match="Invalid format for taught inputs: box_total"):
                main()
            assert worker.call_count == 2
        else:
            assert main() == 0
            loaded = TaskInputRecord.model_validate_json((next_case / "record.json").read_bytes())
            assert loaded.inputs == {"reference": "A-17", "box_total": "23 CTNS"}
            assert loaded.provenance.inputs["box_total"].unit_reference == reference
            taught = TaskInstruction.model_validate_json(amendment.read_bytes())
            assert screen_input_values(loaded, taught, {}) == {
                "reference": "A-17",
                "box_total": "23",
            }
