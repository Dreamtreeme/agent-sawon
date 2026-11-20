"""Read original MIME messages from a scoped Gmail search or an authenticated connector."""

import base64
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email import policy
from email.headerregistry import Address
from email.parser import BytesHeaderParser
from pathlib import Path
from typing import TextIO
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials

from pipeline.inbox_sources import SourceItem, read_mail_bytes

_MESSAGES = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
_KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class MailScope:
    subject: str | None
    sender: str | None
    on_date: date | None

    def query(self) -> str:
        if self.sender is not None:
            if self.on_date is None:
                raise ValueError("Sender discovery requires --on-date")
            address = Address(addr_spec=self.sender)
            if not address.domain or address.addr_spec != self.sender or '"' in self.sender:
                raise ValueError("Supply a single email address for --mail-from")
            query = f'in:inbox from:"{self.sender}"'
        else:
            if not self.subject or not self.subject.strip():
                raise ValueError("Supply --mail-subject or --mail-from")
            query = 'in:inbox subject:"' + self.subject.replace('"', " ") + '"'
        if self.on_date is not None:
            start = datetime.combine(self.on_date, time.min, _KST)
            end = start + timedelta(days=1)
            # Include the whole first second. The local check enforces the exact KST day.
            query += f" after:{int(start.timestamp()) - 1} before:{int(end.timestamp())}"
        return query

    def matches(self, message: dict, raw: bytes) -> bool:
        if self.on_date is not None:
            timestamp = message.get("internalDate")
            labels = message.get("labelIds")
            if not str(timestamp).isdecimal() or not isinstance(labels, list):
                raise ValueError("Date-scoped Gmail originals require internalDate and labelIds")
            received = datetime.fromtimestamp(int(timestamp) // 1000, _KST)
            if "INBOX" not in labels or received.date() != self.on_date:
                return False
        headers = BytesHeaderParser(policy=policy.default).parsebytes(raw)
        if self.sender is None:
            return str(headers.get("Subject", "")) == self.subject
        authors = headers.get_all("From", [])
        return (
            len(authors) == 1
            and len(authors[0].addresses) == 1
            and authors[0].addresses[0].addr_spec.casefold() == self.sender.casefold()
        )


def _source_items(message: dict, mailbox: str, scope: MailScope | None) -> Iterator[SourceItem]:
    if not isinstance(message.get("id"), str) or not message["id"].strip():
        raise ValueError("The Gmail original must identify its source message")
    encoded = message.get("raw")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("The Gmail original must contain the raw MIME data")
    raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    if scope is None or scope.matches(message, raw):
        yield read_mail_bytes(raw, mailbox)


def gmail_items(
    token_file: Path, query: str, mailbox: str, *, scope: MailScope | None = None
) -> Iterator[SourceItem]:
    """Fetch all matching originals using an explicitly supplied authorized-user OAuth file."""
    if not query.strip():
        raise ValueError("A Gmail search query is required")
    credentials = Credentials.from_authorized_user_file(str(token_file), scopes=[_READ_SCOPE])
    params = {"q": query, "fields": "messages/id,nextPageToken", "maxResults": "500"}
    with AuthorizedSession(credentials) as session:
        while True:
            response = session.get(_MESSAGES, params=params, timeout=30)
            response.raise_for_status()
            page = response.json()
            for match in page.get("messages", []):
                message_id = match["id"]
                original = session.get(
                    f"{_MESSAGES}/{quote(message_id, safe='')}",
                    params={"format": "raw", "fields": "id,raw,internalDate,labelIds"},
                    timeout=30,
                )
                original.raise_for_status()
                message = original.json()
                if message["id"] != message_id:
                    raise ValueError("Gmail returned an original for a different message")
                yield from _source_items(message, mailbox, scope)
            token = page.get("nextPageToken")
            if not token:
                return
            params["pageToken"] = token


def connector_gmail_items(
    stream: TextIO, mailbox: str, *, scope: MailScope | None = None
) -> Iterator[SourceItem]:
    """Receive [{id, raw}, ...] after the connector host performed the scoped list/get."""
    messages = json.load(stream)
    if not isinstance(messages, list):
        raise ValueError("Expected a JSON list of Gmail originals")
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Expected an object for each Gmail original")
        yield from _source_items(message, mailbox, scope)
