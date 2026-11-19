import json
import sys
from decimal import Decimal

import pytest

from models.base import MeasuredModelClient, ProviderReply, TokenUsage
from pipeline.inbox import collect, desktop_turn, main, pending
from pipeline.inbox_sources import SourceItem, read_mail_bytes


@pytest.fixture
def planner(tmp_path, monkeypatch, capsys):
    """Exercise the normal CLI, schema validation and accounting using synthetic replies."""
    replies = []
    prompts = []
    state = tmp_path / "desk"

    class Client(MeasuredModelClient):
        def _generate(self, prompt, schema, *, images):
            prompts.append(prompt)
            return ProviderReply(
                text=json.dumps({"decision": replies.pop(0)}),
                usage=TokenUsage(input_tokens=20, response_tokens=15),
            )

        def _cost(self, usage):
            return Decimal(0)

    def create_client(config_path, *, run_log, env_path):
        return Client(provider="local", model="synthetic-planner", run_log=run_log)

    monkeypatch.setattr("pipeline.inbox_planning.create_model_client", create_client)

    def turn(*options):
        monkeypatch.setattr(sys, "argv", ["pipeline.inbox", "--state", str(state), *options])
        assert main() == 0
        return json.loads(capsys.readouterr().out)

    return state, replies, prompts, turn


def chat(key, body):
    return SourceItem(
        "kakao_message",
        "private-room",
        key,
        f"2026-11-19 12:05 [Private Sender]\n{body}",
        body.encode(),
    )


def seed_pair(state):
    mail = read_mail_bytes(
        b"From: hidden@example.invalid\r\nTo: receiving@example.invalid\r\n"
        b"Subject: Register shipment T-71\r\n\r\nT-71: House B/L TEST456. Reopen after saving.",
        "private-inbox",
    )
    packing = chat("packing", "T-71 packing: 12 CTNS.")
    with desktop_turn(state) as db:
        assert collect(db, [mail, packing]) == [1, 2]
    return mail.raw, packing.raw


def work():
    return {
        "kind": "work",
        "work_id": "T-71",
        "goal": "Register T-71 with TEST456 and 12 CTNS, then reopen by House B/L.",
        "reason": "Mail and the packing message share T-71.",
        "receipt_ids": [1, 2],
        "facts": [
            {"key": "hbl_no", "value": "TEST456", "receipt_id": 1},
            {"key": "packages", "value": "12 CTNS", "receipt_id": 2},
        ],
        "supersedes_plan_id": None,
    }


def test_cli_combines_stored_sources_and_skips_model_on_restart(planner, monkeypatch):
    state, replies, prompts, turn = planner
    originals = seed_pair(state)
    decision = work()
    replies.append(decision)
    result = turn("plan-next")
    assert result["status"] == "planned" and result["model_calls"] == 1
    (prompt,) = prompts
    assert all(text in prompt for text in ("Register shipment T-71", "TEST456", "12 CTNS"))
    assert all(
        private not in prompt
        for private in ("hidden@example.invalid", "receiving@example.invalid", "Private Sender")
    )
    (plan,) = turn("plans")
    assert plan["id"] == result["plan_id"] and plan["receipt_ids"] == [1, 2]
    assert plan["stage"] == "planned" and plan["decision"]["facts"] == decision["facts"]
    with desktop_turn(state) as db:
        rows = db.execute("SELECT raw, plan_id FROM receipts ORDER BY id").fetchall()
        assert [(r["raw"], r["plan_id"]) for r in rows] == [(raw, plan["id"]) for raw in originals]
    monkeypatch.setattr("pipeline.inbox_planning.create_model_client", pytest.fail)
    repeated = turn("plan-next", "--model-config", "missing.toml", "--env-path", "missing.env")
    assert repeated["status"] == "empty" and repeated["plan_id"] is None
    assert repeated["model_calls"] == 0 and turn("plans") == [plan]


def test_cli_groups_subset_and_files_reference_without_dispatch(planner):
    state, replies, prompts, turn = planner
    seed_pair(state)
    with desktop_turn(state) as db:
        assert collect(db, [chat("circular", "Office opening hours for your reference.")]) == [3]
    replies.extend([work(), {"kind": "reference", "receipt_ids": [3], "reason": "Office hours."}])
    plan_id = turn("plan-next")["plan_id"]
    assert [r["id"] for r in turn("pending")] == [3]
    reference = turn("plan-next")
    assert reference["status"] == "reference" and reference["plan_id"] is None
    assert turn("pending") == [] and turn("plan-next")["model_calls"] == 0
    assert len(prompts) == 2
    (plan,) = turn("plans")
    assert plan["id"] == plan_id and plan["receipt_ids"] == [1, 2]
    with desktop_turn(state) as db:
        assert db.execute("SELECT reason FROM inbox_references WHERE receipt_id=3").fetchone()[0]


def test_correction_replaces_unstarted_plan_and_keeps_source_of_unchanged_fact(planner):
    state, replies, prompts, turn = planner
    seed_pair(state)
    original = work()
    replies.append(original)
    first = turn("plan-next")["plan_id"]
    with desktop_turn(state) as db:
        assert collect(db, [chat("correction", "T-71 correction: use 18 CTNS.")]) == [3]
    corrected_fact = {"key": "packages", "value": "18 CTNS", "receipt_id": 3}
    replies.append(
        {
            **original,
            "receipt_ids": [3],
            "goal": "Register T-71 with TEST456 and 18 CTNS, then reopen by House B/L.",
            "reason": "The sender corrected the package count before registration.",
            "facts": [corrected_fact],
            "supersedes_plan_id": first,
        }
    )
    second = turn("plan-next")["plan_id"]
    assert "TEST456" in prompts[-1] and "18 CTNS" in prompts[-1]
    old, new = turn("plans")
    assert (old["id"], old["stage"], old["receipt_ids"]) == (first, "superseded", [1, 2])
    assert (new["id"], new["stage"], new["receipt_ids"]) == (second, "planned", [3])
    facts = {fact["key"]: fact for fact in new["decision"]["facts"]}
    assert facts == {"hbl_no": original["facts"][0], "packages": corrected_fact}


@pytest.mark.parametrize(
    ("selected", "source"), [([1, 99], 1), ([1], 2)], ids=["unknown-receipt", "unselected-fact"]
)
def test_invalid_model_links_leave_all_sources_pending(planner, selected, source):
    state, replies, _, turn = planner
    seed_pair(state)
    decision = work()
    decision.update(receipt_ids=selected, facts=[{**decision["facts"][0], "receipt_id": source}])
    replies.append(decision)
    with pytest.raises(ValueError):
        turn("plan-next")
    with desktop_turn(state) as db:
        assert [r["id"] for r in pending(db)] == [1, 2]
        assert db.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0
