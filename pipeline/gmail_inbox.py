"""Read original MIME messages from a scoped Gmail search or an authenticated connector."""

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials

from pipeline.inbox_sources import SourceItem, read_mail_bytes

_MESSAGES = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def _source_item(message: dict, mailbox: str) -> SourceItem:
    if not isinstance(message.get("id"), str) or not message["id"].strip():
        raise ValueError("The Gmail original must identify its source message")
    encoded = message.get("raw")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("The Gmail original must contain the raw MIME data")
    raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    return read_mail_bytes(raw, mailbox)


def gmail_items(token_file: Path, query: str, mailbox: str) -> Iterator[SourceItem]:
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
                    params={"format": "raw", "fields": "id,raw"},
                    timeout=30,
                )
                original.raise_for_status()
                message = original.json()
                if message["id"] != message_id:
                    raise ValueError("Gmail returned an original for a different message")
                yield _source_item(message, mailbox)
            token = page.get("nextPageToken")
            if not token:
                return
            params["pageToken"] = token


def connector_gmail_items(stream: TextIO, mailbox: str) -> Iterator[SourceItem]:
    """Receive [{id, raw}, ...] after the connector host performed the scoped list/get."""
    messages = json.load(stream)
    if not isinstance(messages, list):
        raise ValueError("Expected a JSON list of Gmail originals")
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Expected an object for each Gmail original")
        yield _source_item(message, mailbox)
