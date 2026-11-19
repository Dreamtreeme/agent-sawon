import argparse
import json

import portalocker
import pytest

from brain import run
from pipeline.inbox import collect, desktop_turn, pending, record_plan, run_worker
from pipeline.inbox_sources import read_chat, read_mail


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
