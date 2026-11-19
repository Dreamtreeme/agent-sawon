"""Read original inbound artifacts for a named mailbox or KakaoTalk room."""

from __future__ import annotations

import codecs
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from email import policy
from email.parser import BytesParser
from itertools import pairwise
from pathlib import Path


@dataclass(frozen=True)
class SourceItem:
    source: str
    channel: str
    key: str
    text: str
    raw: bytes


def read_mail(path: Path, channel: str) -> SourceItem:
    """Read one RFC 5322 original, keeping attachments only inside its raw bytes."""
    return read_mail_bytes(path.read_bytes(), channel)


def read_mail_bytes(raw: bytes, channel: str) -> SourceItem:
    """Use the same original and receipt identity for live and exported email."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    header = message["Message-ID"]
    match = re.fullmatch(r"<([^<>\s]+)@([^<>\s]+)>", str(header).strip())
    if match and not header.defects:
        local, domain = match.groups()
        key = f"message-id:{local}@{domain.lower()}"
    else:
        key = "sha256:" + hashlib.sha256(raw).hexdigest()
    metadata = ("From", "To", "Date", "Subject", "In-Reply-To", "References")
    headers = [f"{name}: {message[name]}" for name in metadata if message[name]]
    part = message.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part is not None else ""
    if part is not None and part.get_content_type() == "text/html":
        body = "[HTML body]\n" + body
    return SourceItem("email", channel, key, "\n".join(headers) + "\n\n" + body, raw)


def mail_updates(directory: Path, channel: str) -> Iterator[SourceItem]:
    """Read individual .eml originals in one mailbox directory in stable order."""
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix.lower() == ".eml":
            yield read_mail(path, channel)


def _decode_chat(raw: bytes) -> tuple[str, str]:
    for bom, encoding in ((codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
        if raw.startswith(bom):
            return raw.decode(encoding), encoding
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp949"), "cp949"


def read_chat(path: Path, channel: str) -> SourceItem | None:
    """Read a Windows text export as one room snapshot with its full original bytes."""
    raw = path.read_bytes()
    transcript, _ = _decode_chat(raw)
    lines = transcript.removeprefix("\ufeff").splitlines()
    if not any(line.strip() for line in lines):
        return None
    # A Korean Windows export starts with "<room> 님과 카카오톡 대화", then its save time.
    # Only remove that header's timestamp; preserve even identically worded message lines.
    if lines[0].rstrip().endswith("카카오톡 대화"):
        first = next((i for i in range(1, len(lines)) if lines[i].strip()), None)
        if first is not None and re.match(r"^저장한 날짜\s*:\s*\d{4}", lines[first].strip()):
            del lines[first]
        if not any(line.strip() for line in lines[1:]):
            return None
    text = "\n".join(lines).rstrip("\n")
    key = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    return SourceItem("kakao", channel, key, text, raw)


_CHAT_DAY = re.compile(
    r"^-{3,}\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일(?:\s+[월화수목금토일]요일)?\s*-{3,}$"
)
_CHAT_HEADER = re.compile(r"^\[(.+?)\] \[(오전|오후) (1[0-2]|0?[1-9]):([0-5]\d)\](?: |$)")


def _chat_boundaries(
    lines: list[str], encoding: str
) -> Iterator[tuple[int, str, re.Match[str] | None]]:
    """Yield original byte offsets; day dividers also end the preceding message."""
    offset = len(lines[0].encode(encoding))
    day = ""
    in_message = False
    for line in lines[1:]:
        content = line.rstrip("\r\n")
        divider = _CHAT_DAY.fullmatch(content)
        header = _CHAT_HEADER.match(content)
        if divider:
            day = date(*(int(value) for value in divider.groups())).isoformat()
            in_message = False
            yield offset, day, None
        elif header:
            if not day:
                raise ValueError("KakaoTalk message is missing its export date divider")
            in_message = True
            yield offset, day, header
        elif re.match(r"^\[.+?\]\s+\[(?:오전|오후|AM|PM|\d{1,2}:)|^-{3,}\s*\d{4}", content):
            raise ValueError("Unrecognized KakaoTalk message boundary")
        elif not in_message and content.strip():
            if day or not re.fullmatch(r"저장한 날짜\s*:\s*\d{4}.*", content.strip()):
                raise ValueError("Unrecognized KakaoTalk Windows export layout")
        offset += len(line.encode(encoding))
    yield offset, day, None


def chat_messages(path: Path, *, room_title: str, channel: str, marker: str) -> list[SourceItem]:
    """Retain only matching message blocks from an explicitly named Korean Windows room."""
    if not room_title.strip() or not marker.strip() or not channel.strip():
        raise ValueError("Room title, channel and marker are required")
    raw = path.read_bytes()
    transcript, encoding = _decode_chat(raw)
    lines = transcript.splitlines(keepends=True)
    title = lines[0].removeprefix("\ufeff").rstrip("\r\n") if lines else ""
    if title not in {f"{room_title}{space}님과 카카오톡 대화" for space in ("", " ")}:
        raise ValueError("KakaoTalk export header does not match the requested room")
    matches = []
    occurrences: Counter[str] = Counter()
    for (start, day, header), (end, _, _) in pairwise(_chat_boundaries(lines, encoding)):
        if header is None:
            continue
        block = raw[start:end]
        normalized = "\n".join(block.decode(encoding).splitlines()).rstrip("\n")
        body = normalized[header.end() :]
        if marker not in body:
            continue
        sender, period, hour, minute = header.groups()
        timestamp = f"{day} {int(hour) % 12 + 12 * (period == '오후'):02}:{minute}"
        identity = json.dumps([timestamp, sender, body], ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(identity).hexdigest()
        occurrences[digest] += 1
        matches.append(
            SourceItem(
                "kakao_message",
                channel,
                f"sha256:{digest}:{occurrences[digest]}",
                f"{timestamp} [{sender}]\n{body}",
                block,
            )
        )
    return matches
