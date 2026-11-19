import base64
import hashlib
import io
import json
import sys

import pytest

from pipeline.inbox import desktop_turn, main


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
