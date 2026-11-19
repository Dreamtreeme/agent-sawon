"""One PC's serial inbox and handoff to the existing GUI worker."""

import argparse
import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path

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
        FROM receipts WHERE plan_id IS NULL AND kind != 'baseline' ORDER BY id
    """)
    return [dict(row) for row in rows]


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
    commands.add_parser("pending", help="Show unplanned text and source IDs")
    commands.add_parser("plans", help="Show recorded decisions, source IDs and worker status")
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
        elif args.command == "pending":
            output = pending(db)
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
