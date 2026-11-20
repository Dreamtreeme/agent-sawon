import base64
import hashlib
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from pipeline.inbox import desktop_turn, main

MIXED_INBOX = Path(__file__).parent / "fixtures" / "mixed_inbox" / "arrival"


def test_live_cli_rereads_gmail_then_kakao_after_partial_failure(tmp_path, monkeypatch, capsys):
    """Synthetic connector responses and GUI exports exercise the real CLI and SQLite ledger."""
    state = tmp_path / "state"
    ocr = tmp_path / "ocr.toml"
    ocr.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pipeline.inbox",
            "--state",
            str(state),
            "collect-live",
            "--gmail-raw-stdin",
            "--mailbox",
            "purchasing",
            "--mail-subject",
            "Order",
            "--room",
            "supplier",
            "--room-title",
            "시험방",
            "--chat-marker",
            "TEST-42",
            "--relay-url",
            "http://localhost:1",
            "--ocr-config",
            str(ocr),
        ],
    )
    monkeypatch.setenv("SAWON_RELAY_TOKEN", "synthetic")
    monkeypatch.setattr("brain.relay.HttpRelay.health", lambda self: {})
    selected = b"Message-ID: <a@test.invalid>\r\nSubject: Order\r\n\r\n24 cartons"
    noise = b"Message-ID: <b@test.invalid>\r\nSubject: Older Order\r\n\r\nSeparate transaction"
    originals = json.dumps(
        [
            {"id": str(n), "raw": base64.urlsafe_b64encode(raw).decode("ascii")}
            for n, raw in enumerate((selected, noise), 1)
        ]
    )
    message = "[공급자] [오후 1:00] TEST-42의 수량은 30입니다.\n"
    exports = []

    def fake_export(relay, room, path, *, ocr, self_chat):
        assert sys.stdin.tell() == len(originals)
        assert not path.exists()
        exports.append(path)
        if len(exports) == 1:
            raise RuntimeError("synthetic_room_unavailable")
        path.write_bytes(
            (
                f"{room} 님과 카카오톡 대화\n저장한 날짜 : 2026-11-19 13:0{len(exports)}\n"
                f"--------------- 2026년 11월 19일 목요일 ---------------\n{message}"
                "[공급자] [오후 1:03] 별도 주문입니다.\n"
            ).encode()
        )

    monkeypatch.setattr("pipeline.kakao_inbox.export_kakao_chat", fake_export)

    def turn():
        monkeypatch.setattr(sys, "stdin", io.StringIO(originals))
        assert main() == 0
        return json.loads(capsys.readouterr().out)

    with pytest.raises(RuntimeError, match="synthetic_room_unavailable"):
        turn()
    with desktop_turn(state) as db:
        assert [row["raw"] for row in db.execute("SELECT raw FROM receipts")] == [selected]
    recovered = turn()
    repeated = turn()
    assert recovered["gmail"]["new_receipt_ids"] == []
    assert len(recovered["kakao"]["new_receipt_ids"]) == 1
    for source, raw in (("gmail", selected), ("kakao", message.encode("utf-8"))):
        assert repeated[source]["read"] == 1
        assert repeated[source]["source_key_hashes"] == recovered[source]["source_key_hashes"]
        assert repeated[source]["raw_sha256"] == [hashlib.sha256(raw).hexdigest()]
        assert repeated[source]["new_receipt_ids"] == []
    assert len(set(exports)) == 3 and all(not path.parent.exists() for path in exports)
    with desktop_turn(state) as db:
        stored = db.execute("SELECT raw FROM receipts ORDER BY id").fetchall()
        assert [row["raw"] for row in stored] == [selected, message.encode("utf-8")]


def test_synthetic_discovery_by_sender_room_and_date_with_unknown_message_contents(
    tmp_path, monkeypatch, capsys
):
    """Synthetic Gmail receipt metadata and chat export enter the ordinary CLI twice."""
    state = tmp_path / "state"
    ocr = tmp_path / "ocr.toml"
    ocr.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pipeline.inbox",
            "--state",
            str(state),
            "collect-live",
            "--gmail-raw-stdin",
            "--mailbox",
            "purchasing",
            "--mail-from",
            "shipping@daehwa.example.invalid",
            "--room",
            "supplier",
            "--room-title",
            "대화부품 수출팀",
            "--on-date",
            "2026-11-19",
            "--relay-url",
            "http://localhost:1",
            "--ocr-config",
            str(ocr),
        ],
    )
    monkeypatch.setattr("brain.relay.HttpRelay.health", lambda self: {})
    monkeypatch.setenv("SAWON_RELAY_TOKEN", "synthetic")
    original = (MIXED_INBOX / "incoming.eml").read_bytes()
    midnight = int(datetime.fromisoformat("2026-11-19T00:00:00+09:00").timestamp()) * 1000
    ms_per_day = 24 * 3600 * 1000
    before = original.replace(b"request-1119-04", b"before")
    after = original.replace(b"request-1119-04", b"after")
    another_sender = original.replace(b"shipping@", b"shipping2@")
    sent = original.replace(b"request-1119-04", b"sent")
    packet = [
        {
            "id": str(i),
            "raw": base64.urlsafe_b64encode(raw).decode(),
            "internalDate": str(ms),
            "labelIds": labels,
        }
        for i, (raw, ms, labels) in enumerate(
            [
                (before, midnight - 1, ["INBOX"]),
                (original, midnight, ["INBOX"]),
                (after, midnight + ms_per_day, ["INBOX"]),
                (another_sender, midnight, ["INBOX"]),
                (sent, midnight, ["SENT"]),
            ]
        )
    ]
    chat = (MIXED_INBOX / "kakao.txt").read_text(encoding="utf-8")

    def fake_export(relay, room, path, *, ocr, self_chat):
        path.write_text(
            chat + "\n--------------- 2026년 11월 20일 금요일 ---------------\n"
            "[대화부품 수출팀] [오전 12:00] 내일의 메시지\n",
            encoding="utf-8",
        )

    monkeypatch.setattr("pipeline.kakao_inbox.export_kakao_chat", fake_export)
    for expected in ([1, 2], [0, 0]):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(packet)))
        assert main() == 0
        result = json.loads(capsys.readouterr().out)
        assert [len(result[source]["new_receipt_ids"]) for source in ("gmail", "kakao")] == expected
    with desktop_turn(state) as db:
        rows = db.execute("SELECT raw,text FROM receipts ORDER BY id").fetchall()
        assert rows[0]["raw"] == original
        assert "참고" in rows[1]["text"] and "정정" in rows[2]["text"]
