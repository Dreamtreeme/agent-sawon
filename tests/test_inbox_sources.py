from email import policy
from email.message import EmailMessage

import pytest

from pipeline.inbox_sources import chat_messages, mail_updates, read_chat, read_mail


def test_mail_keeps_original_and_reads_only_preferred_body(tmp_path):
    message = EmailMessage(policy=policy.SMTP)
    message["Message-ID"] = "<Request-17@EXAMPLE.COM>"
    message["From"] = "sender@example.com"
    message["Subject"] = "수량 정정"
    message["In-Reply-To"] = "<previous@example.com>"
    message.set_content("수량 30")
    message.add_alternative("<p>HTML alternative</p>", subtype="html")
    message.add_attachment("unrelated text", filename="attachment.txt")
    raw = message.as_bytes()
    (tmp_path / "letter.EML").write_bytes(raw)
    (tmp_path / "attachment.txt").write_text("ignored", encoding="utf-8")

    (item,) = mail_updates(tmp_path, "desk")
    assert (item.source, item.channel, item.key) == (
        "email",
        "desk",
        "message-id:Request-17@example.com",
    )
    assert item.raw == raw
    assert "수량 정정" in item.text and "수량 30" in item.text
    assert "In-Reply-To: <previous@example.com>" in item.text
    assert "HTML alternative" not in item.text and "unrelated text" not in item.text


def test_html_only_mail_without_message_id_uses_original_bytes_for_identity(tmp_path):
    path = tmp_path / "sample.eml"
    raw = b"From: supply@example.com\r\nContent-Type: text/html\r\n\r\n<p>revised</p>"
    path.write_bytes(raw)
    first = read_mail(path, "purchasing")
    assert first.key.startswith("sha256:") and "[HTML body]\n<p>revised</p>" in first.text
    assert read_mail(path, "purchasing").key == first.key
    path.write_bytes(raw + b"extra")
    assert read_mail(path, "purchasing").key != first.key


def test_chat_reexports_preserve_message_text_and_ignore_only_header_save_time(tmp_path):
    path = tmp_path / "room.txt"
    header = "시험방 님과 카카오톡 대화\r\n저장한 날짜 : 2026-11-19 "
    body = "\r\n\r\n[담당자] [오후 1:03] 진행\r\n저장한 날짜 : 2026-11-19 14:00\r\n"
    first_raw = (header + "13:05" + body).encode("cp949")
    path.write_bytes(first_raw)
    first = read_chat(path, "시험방")
    assert first.raw == first_raw and first.source == "kakao"
    assert "13:05" not in first.text and "14:00" in first.text
    path.write_bytes((header + "13:10" + body).encode("utf-16"))
    assert read_chat(path, "시험방").key == first.key
    path.write_text(header + "13:10" + body + "[담당자] [오후 1:03] 진행", encoding="utf-8-sig")
    assert read_chat(path, "시험방").key != first.key
    path.write_text(header + "13:10\r\n", encoding="utf-8")
    assert read_chat(path, "시험방") is None


def test_live_chat_reads_only_the_named_room_and_original_matching_messages(tmp_path):
    path = tmp_path / "chat.txt"
    title = "시험방 님과 카카오톡 대화\r\n저장한 날짜 : 2026-11-19 "
    day = "--------------- 2026년 11월 19일 목요일 ---------------\r\n"
    selected = "[거래처] [오후 2:05] 출고 자료\r\n식별번호 B / 수량 30\r\n"
    before = "[다른사람] [오후 2:04] 다른 업무\r\n"
    after = "[다른사람] [오후 2:06] 별개 연락\r\n"
    selection = {"room_title": "시험방", "channel": "trial", "marker": "식별번호 B"}
    path.write_bytes((title + "14:10\r\n" + day + before + selected + after).encode("cp949"))
    (first,) = chat_messages(path, **selection)
    assert first.raw == selected.encode("cp949")
    assert first.text == "2026-11-19 14:05 [거래처]\n출고 자료\n식별번호 B / 수량 30"
    assert (first.channel, first.source) == ("trial", "kakao_message")
    path.write_bytes((title + "14:20\r\n" + day + before + selected * 2 + after).encode("utf-16"))
    original, duplicate = chat_messages(path, **selection)
    assert original.raw == selected.encode("utf-16-le")
    assert original.key == first.key and duplicate.key != original.key
    assert duplicate.text == original.text
    assert chat_messages(path, **{**selection, "marker": "존재하지 않음"}) == []
    with pytest.raises(ValueError, match="requested room"):
        chat_messages(path, **{**selection, "room_title": "다른 시험방"})
    path.write_text(
        title + "14:10\n" + day + selected + "[다른사람] [PM 02:06] 다른 업무",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="message boundary"):
        chat_messages(path, **selection)
    path.write_text(title + "14:10\n식별번호 B incomplete exported text", encoding="utf-8")
    with pytest.raises(ValueError, match="layout"):
        chat_messages(path, **selection)
