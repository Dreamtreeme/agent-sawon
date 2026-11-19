"""One PC's serial inbox and handoff to the existing GUI worker."""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from decimal import Decimal
from email import policy
from email.parser import BytesHeaderParser
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import portalocker

from pipeline.inbox_sources import SourceItem, mail_updates, read_chat


@contextmanager
def desktop_turn(state: Path) -> Iterator[sqlite3.Connection]:
    state.mkdir(parents=True, exist_ok=True)
    with (
        portalocker.Lock(state / "desktop.lock", flags=portalocker.LockFlags.EXCLUSIVE),
        closing(sqlite3.connect(state / "inbox.db")) as db,
    ):
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY, decision TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'planned', worker_exit INTEGER, worker_args TEXT
            );
            CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL, channel TEXT NOT NULL,
                source_key TEXT NOT NULL, raw BLOB NOT NULL, text TEXT NOT NULL,
                kind TEXT NOT NULL, new_text TEXT NOT NULL,
                collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                plan_id INTEGER REFERENCES plans(id), UNIQUE(source, channel, source_key)
            );
            CREATE TABLE IF NOT EXISTS inbox_references (
                receipt_id INTEGER PRIMARY KEY REFERENCES receipts(id), reason TEXT NOT NULL
            );
        """)
        yield db


def collect(
    db: sqlite3.Connection, items: Iterable[SourceItem], *, baseline_chat: bool = False
) -> list[int]:
    added = []
    with db:
        for item in items:
            kind, new_text = "message", item.text
            if item.source == "kakao":
                previous = db.execute(
                    """SELECT text FROM receipts WHERE source=? AND channel=?
                       ORDER BY id DESC LIMIT 1""",
                    (item.source, item.channel),
                ).fetchone()
                if previous is None:
                    kind, new_text = ("baseline", "") if baseline_chat else ("initial", item.text)
                else:
                    before = previous["text"].rstrip("\n")
                    if not before or item.text.startswith(before + "\n"):
                        kind, new_text = "append", item.text[len(before) :].lstrip("\n")
                    else:
                        kind, new_text = "review", item.text
            cursor = db.execute(
                """INSERT INTO receipts(source,channel,source_key,raw,text,kind,new_text)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(source,channel,source_key) DO NOTHING""",
                (item.source, item.channel, item.key, item.raw, item.text, kind, new_text),
            )
            if cursor.rowcount:
                added.append(cursor.lastrowid)
    return added


def pending(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute("""
        SELECT id,source,channel,source_key,collected_at,kind,new_text
        FROM receipts WHERE plan_id IS NULL AND kind != 'baseline'
          AND NOT EXISTS (SELECT 1 FROM inbox_references WHERE receipt_id = receipts.id)
        ORDER BY id
    """)
    return [dict(row) for row in rows]


def collect_live(db: sqlite3.Connection, args: argparse.Namespace) -> dict:
    from brain.relay import HttpRelay
    from pipeline.gmail_inbox import connector_gmail_items, gmail_items
    from pipeline.inbox_sources import chat_messages
    from pipeline.kakao_inbox import export_kakao_chat
    from pipeline.ocr import PaddleOcrEngine
    from pipeline.schemas import PipelineSettings

    def save(items: Iterable[SourceItem]) -> dict:
        keys = []
        raw_hashes = []

        def observed():
            for item in items:
                keys.append(hashlib.sha256(item.key.encode("utf-8")).hexdigest())
                raw_hashes.append(hashlib.sha256(item.raw).hexdigest())
                yield item

        added = collect(db, observed())
        return {
            "read": len(keys),
            "source_key_hashes": keys,
            "raw_sha256": raw_hashes,
            "new_receipt_ids": added,
        }

    query = 'in:inbox subject:"' + args.mail_subject.replace('"', " ") + '"'
    mail = (
        connector_gmail_items(sys.stdin, args.mailbox)
        if args.gmail_raw_stdin
        else gmail_items(args.gmail_token, query, args.mailbox)
    )
    headers = BytesHeaderParser(policy=policy.default)
    gmail_result = save(
        item
        for item in mail
        if str(headers.parsebytes(item.raw).get("Subject", "")) == args.mail_subject
    )
    relay = HttpRelay(args.relay_url, os.environ.get("SAWON_RELAY_TOKEN", ""))
    relay.health()
    # The whole-room export and OCR captures live only inside this private turn.
    with TemporaryDirectory(prefix="sawon-chat-") as folder:
        destination = Path(folder) / "conversation.txt"
        ocr = PaddleOcrEngine(PipelineSettings.load(args.ocr_config))
        export_kakao_chat(relay, args.room_title, destination, ocr=ocr, self_chat=args.self_chat)
        kakao_result = save(
            chat_messages(
                destination, room_title=args.room_title, channel=args.room, marker=args.chat_marker
            )
        )
    return {"gmail": gmail_result, "kakao": kakao_result}


def record_plan(db: sqlite3.Connection, ids: list[int], decision: dict) -> int:
    if not ids or len(set(ids)) != len(ids) or not decision:
        raise ValueError("Supply distinct receipt IDs and a nonempty plan object")
    encoded = json.dumps(decision, ensure_ascii=False, sort_keys=True, allow_nan=False)
    with db:
        if "supersedes_plan_id" in decision:
            previous = decision["supersedes_plan_id"]
            if type(previous) is not int:
                raise ValueError("supersedes_plan_id must be an integer")
            replaced = db.execute(
                "UPDATE plans SET stage='superseded' WHERE id=? AND stage='planned'", (previous,)
            )
            if replaced.rowcount != 1:
                raise ValueError("Only an existing unstarted plan can be superseded")
        plan = db.execute("INSERT INTO plans(decision) VALUES(?)", (encoded,))
        for receipt in ids:
            linked = db.execute(
                "UPDATE receipts SET plan_id=? WHERE id=? AND kind!='baseline' AND plan_id IS NULL",
                (plan.lastrowid, receipt),
            )
            if linked.rowcount != 1:
                raise ValueError(f"Receipt {receipt} is missing, a baseline or already planned")
    return int(plan.lastrowid)


def plan_next(db: sqlite3.Connection, args: argparse.Namespace) -> dict:
    """Record one model decision against the same locked snapshot it was given."""
    receipts = pending(db)
    if not receipts:
        return {"status": "empty", "plan_id": None, "model_calls": 0}

    from models.accounting import read_model_accounting
    from pipeline.inbox_planning import Reference, Wait, choose
    from sawon_runtime.storage import RunLog

    unstarted = [
        {"id": row["id"], "decision": json.loads(row["decision"])}
        for row in db.execute("SELECT id,decision FROM plans WHERE stage='planned' ORDER BY id")
    ]
    run_id = uuid4().hex
    log = RunLog(args.state / "planning" / run_id, case_id=run_id, agent_id="inbox-planner")
    selection = choose(
        receipts,
        unstarted,
        log=log,
        model_config=args.model_config,
        env_path=args.env_path,
        max_cost_usd=args.max_cost_usd,
    )
    result = {
        "plan_id": None,
        "run_id": run_id,
        "model_calls": read_model_accounting(log).model_calls,
    }
    if isinstance(selection, Wait):
        return {**result, "status": "wait", "reason": selection.reason}
    ids = selection.receipt_ids
    selected = set(ids)
    if not ids or len(selected) != len(ids) or not selected <= {r["id"] for r in receipts}:
        raise ValueError("Select distinct receipts from this pending turn")
    if isinstance(selection, Reference):
        with db:
            db.executemany(
                "INSERT INTO inbox_references(receipt_id,reason) VALUES (?,?)",
                [(receipt_id, selection.reason) for receipt_id in ids],
            )
        return {**result, "status": "reference", "receipt_ids": ids}

    if any(f.receipt_id not in selected for f in selection.facts):
        raise ValueError("Each new fact must cite a receipt selected for this work")
    decision = selection.model_dump(exclude={"kind", "receipt_ids"}, exclude_none=True)
    if selection.supersedes_plan_id is not None:
        prior = next(
            (p["decision"] for p in unstarted if p["id"] == selection.supersedes_plan_id), None
        )
        if prior is None:
            raise ValueError("Only an existing unstarted plan can be superseded")
        if prior.get("work_id"):
            decision["work_id"] = prior["work_id"]
        changed_keys = {f.key for f in selection.facts}
        if isinstance(prior.get("facts"), list):
            inherited = [
                fact
                for fact in prior["facts"]
                if isinstance(fact, dict)
                and isinstance(fact.get("key"), str)
                and fact["key"] not in changed_keys
            ]
            decision["facts"] = inherited + decision["facts"]
    plan_id = record_plan(db, ids, decision)
    return {**result, "status": "planned", "plan_id": plan_id, "receipt_ids": ids}


def run_worker(db: sqlite3.Connection, plan: int, arguments: list[str]) -> int:
    from brain.run import main as run_case

    row = db.execute("SELECT decision,stage FROM plans WHERE id=?", (plan,)).fetchone()
    if row is None or row["stage"] != "planned":
        raise ValueError(f"Plan {plan} is missing or no longer awaiting dispatch")
    goal = json.loads(row["decision"]).get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("A runnable plan needs a goal")
    command = [*arguments, "--goal", goal]
    # Commit before dispatch. An interrupted run keeps its evidence for reconciliation.
    with db:
        db.execute(
            "UPDATE plans SET stage='started',worker_args=? WHERE id=?",
            (json.dumps(command, ensure_ascii=False), plan),
        )
    try:
        code = run_case(command)
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else 1
    with db:
        db.execute("UPDATE plans SET stage='exited', worker_exit=? WHERE id=?", (code, plan))
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="Private directory for this PC")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("collect", help="Read email exports, then the named chat export")
    scan.add_argument("--mail-dir", type=Path)
    scan.add_argument("--mailbox", help="Stable name of the receiving mailbox")
    scan.add_argument("--chat-export", type=Path)
    scan.add_argument("--room", help="Stable name of the dedicated chat")
    scan.add_argument(
        "--baseline-chat", action="store_true", help="Skip existing text on first import"
    )
    live = commands.add_parser(
        "collect-live", help="Read scoped Gmail originals, then export a chat"
    )
    auth = live.add_mutually_exclusive_group(required=True)
    auth.add_argument("--gmail-token", type=Path, help="Authorized-user Gmail read-only OAuth file")
    auth.add_argument(
        "--gmail-raw-stdin", action="store_true", help="Read fresh connector id/raw JSON"
    )
    live.add_argument("--mailbox", required=True, help="Stable name of the receiving mailbox")
    live.add_argument("--mail-subject", required=True, help="Exact subject of the target mail")
    live.add_argument("--room", required=True, help="Stable name of the designated conversation")
    live.add_argument("--room-title", required=True, help="KakaoTalk's actual chat window title")
    live.add_argument("--self-chat", action="store_true", help="Allow the self-profile navigation")
    live.add_argument("--chat-marker", required=True, help="Text identifying requested messages")
    live.add_argument("--relay-url", required=True)
    live.add_argument("--ocr-config", type=Path, required=True)
    commands.add_parser("pending", help="Show unplanned text and source IDs")
    commands.add_parser("plans", help="Show recorded decisions, source IDs and worker status")
    planner = commands.add_parser(
        "plan-next", help="Use pending source texts to plan one work item"
    )
    planner.add_argument("--model-config", type=Path, default=Path("config/models.toml"))
    planner.add_argument("--env-path", type=Path, help="Explicit .env for a paid model")
    planner.add_argument("--max-cost-usd", type=Decimal, default=Decimal("3"))
    link = commands.add_parser("record-plan", help="Associate source IDs with a JSON decision")
    link.add_argument("--receipts", nargs="+", type=int, required=True)
    link.add_argument("--file", type=Path, required=True)
    worker = commands.add_parser("worker", help="Hold the PC turn for one normal brain.run")
    worker.add_argument("--plan", type=int, required=True)
    worker.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    with desktop_turn(args.state) as db:
        if args.command == "collect":
            if not (args.mail_dir or args.chat_export):
                parser.error("collect requires --mail-dir or --chat-export")
            if (
                bool(args.mail_dir) != bool(args.mailbox)
                or bool(args.chat_export) != bool(args.room)
                or (args.baseline_chat and not args.chat_export)
            ):
                parser.error("Pair --mail-dir with --mailbox and --chat-export with --room")
            added = []
            if args.mail_dir:
                if not args.mail_dir.is_dir():
                    parser.error("Mail directory does not exist")
                added.extend(collect(db, mail_updates(args.mail_dir, args.mailbox)))
            if args.chat_export:
                item = read_chat(args.chat_export, args.room)
                if item is not None:
                    added.extend(collect(db, [item], baseline_chat=args.baseline_chat))
            output = {"new_receipt_ids": added}
        elif args.command == "collect-live":
            output = collect_live(db, args)
        elif args.command == "pending":
            output = pending(db)
        elif args.command == "plan-next":
            output = plan_next(db, args)
        elif args.command == "plans":
            output = []
            for row in db.execute("SELECT * FROM plans ORDER BY id"):
                receipts = db.execute(
                    "SELECT id FROM receipts WHERE plan_id=? ORDER BY id", (row["id"],)
                )
                output.append(
                    {
                        **dict(row),
                        "decision": json.loads(row["decision"]),
                        "worker_args": json.loads(row["worker_args"] or "null"),
                        "receipt_ids": [r["id"] for r in receipts],
                    }
                )
        elif args.command == "record-plan":
            document = json.loads(args.file.read_text(encoding="utf-8-sig"))
            if not isinstance(document, dict):
                parser.error("The plan file must contain a JSON object")
            output = {"plan_id": record_plan(db, args.receipts, document)}
        else:
            arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
            if not arguments or "--help" in arguments or "-h" in arguments:
                parser.error("Provide ordinary brain.run case arguments after --")
            return run_worker(db, args.plan, arguments)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
