import json

from test_inbox_planning import chat, seed_pair, work
from test_inbox_planning import planner as planner

from pipeline.inbox import collect, desktop_turn, record_plan, run_worker
from pipeline.inbox_sources import read_mail_bytes


def test_ambiguous_correction_waits_across_polls_until_an_identifying_reply(planner, monkeypatch):
    """Synthetic model replies and prior ERP exits. Exercise the real inbox CLI and SQLite state."""
    state, replies, prompts, turn = planner
    monkeypatch.setattr("brain.run.main", lambda argv: 0)
    with desktop_turn(state) as db:
        for identity in ("BL-A", "BL-B"):
            mail = read_mail_bytes(
                f"Subject: North Logistics\r\n\r\n{identity}, 18 CTNS".encode(), "desk"
            )
            (receipt,) = collect(db, [mail])
            facts = [
                {"key": key, "label": label, "value": value, "receipt_id": receipt}
                for key, label, value in [
                    ("hbl", "House B/L", identity),
                    ("count", "포장 수량", "18 CTNS"),
                ]
            ]
            plan = record_plan(db, [receipt], {"work_id": identity, "goal": "등록", "facts": facts})
            assert run_worker(db, plan, []) == 0
        correction = chat("change", "아까 그 건 수량을 22 CTNS로 정정해 주세요.")
        (correction_id,) = collect(db, [correction])

    before = turn("plans")
    question = "BL-A와 BL-B 중 어느 House B/L의 수량을 정정할까요?"
    replies.append({"kind": "wait", "reason": question})
    first = turn("plan-next")
    assert (first["status"], first["plan_id"], first["reason"]) == ("wait", None, question)
    with desktop_turn(state) as db:
        assert collect(db, [correction]) == []
    again = turn("plan-next")
    assert again["model_calls"] == 0 and again["reason"] == question
    assert again["waiting_run_id"] == first["run_id"]
    assert [row["id"] for row in turn("pending")] == [correction_id] and turn("plans") == before

    clue = read_mail_bytes(
        b"Subject: Re: North Logistics\r\n\r\nThe correction is for BL-B.", "desk"
    )
    with desktop_turn(state) as db:
        (clue_id,) = collect(db, [clue])
    change = {"key": "count", "label": "포장 수량", "value": "22 CTNS", "receipt_id": correction_id}
    replies.append(
        {
            "kind": "work",
            "work_id": "BL-B",
            "goal": "BL-B의 수량 변경 후 재조회",
            "reason": "후속 메일로 대상을 확인",
            "supersedes_plan_id": before[1]["id"],
            "receipt_ids": [correction_id, clue_id],
            "facts": [change],
        }
    )
    assert turn("plan-next")["status"] == "planned"
    original_request, clarified_request = [json.loads(p.split("\n\n")[-1]) for p in prompts]
    assert original_request["plans"] == clarified_request["plans"]
    assert [p["decision"]["work_id"] for p in clarified_request["plans"]] == ["BL-A", "BL-B"]
    assert [r["id"] for r in original_request["receipts"]] == [correction_id]
    assert [(r["id"], r["source"]) for r in clarified_request["receipts"]] == [
        (correction_id, "kakao_message"),
        (clue_id, "email"),
    ]
    first_plan, second_plan, revision = turn("plans")
    assert [first_plan, second_plan] == before
    assert revision["decision"]["supersedes_plan_id"] == second_plan["id"]
    assert revision["decision"]["facts"] == [second_plan["decision"]["facts"][0], change]
    assert revision["receipt_ids"] == [correction_id, clue_id]
    assert turn("plan-next")["status"] == "empty"


def test_changed_candidate_stage_reopens_wait_without_new_text(planner, monkeypatch):
    state, replies, prompts, turn = planner
    seed_pair(state)
    replies.append(work())
    plan = turn("plan-next")["plan_id"]
    with desktop_turn(state) as db:
        collect(db, [chat("query", "등록한 건을 정정하려면 어떤 번호가 필요할까요?")])
    replies.append({"kind": "wait", "reason": "등록 완료 여부를 확인합니다."})
    assert turn("plan-next")["status"] == "wait"
    monkeypatch.setattr("brain.run.main", lambda argv: 0)
    with desktop_turn(state) as db:
        assert run_worker(db, plan, []) == 0
    replies.append({"kind": "wait", "reason": "수정할 값을 확인합니다."})
    decision = turn("plan-next")
    assert decision["model_calls"] == 1 and decision["reason"] == "수정할 값을 확인합니다."
    before, after = [json.loads(p.split("\n\n")[-1]) for p in prompts[-2:]]
    assert before["receipts"] == after["receipts"]
    assert (before["plans"][0]["stage"], after["plans"][0]["stage"]) == ("planned", "exited")
