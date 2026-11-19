"""Read original inbound artifacts for a named mailbox or KakaoTalk room."""

from __future__ import annotations

import codecs
import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
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
    raw = path.read_bytes()
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


def read_chat(path: Path, channel: str) -> SourceItem | None:
    """Read a Windows text export as one room snapshot with its full original bytes."""
    raw = path.read_bytes()
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        transcript = raw.decode("utf-16")
    else:
        try:
            transcript = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            transcript = raw.decode("cp949")
    lines = transcript.splitlines()
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
