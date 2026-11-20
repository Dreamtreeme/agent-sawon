import base64
import json
from datetime import date, datetime
from io import StringIO
from unittest.mock import Mock

import pytest
from requests import Response

from pipeline import gmail_inbox
from pipeline.inbox import collect, desktop_turn
from pipeline.inbox_sources import read_mail


def gmail_original(number):
    mime = (
        f"Message-ID: <mail-{number}@sender.invalid>\r\nSubject: Update\r\n\r\n"
        f"Original body {number}\r\n"
    ).encode()
    return {"id": str(number), "raw": base64.urlsafe_b64encode(mime).decode().rstrip("=")}, mime


def test_scoped_gmail_read_follows_pages_and_matches_connector_and_export(tmp_path, monkeypatch):
    one, mime_one = gmail_original(1)
    two, mime_two = gmail_original(2)
    requested = []
    query = 'in:inbox subject:"delivery update"'

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, *, params, timeout):
            requested.append((url, params.copy()))
            if url.endswith("/messages"):
                assert params["q"] == query
                body = (
                    {"messages": [{"id": "2"}]}
                    if params.get("pageToken") == "page2"
                    else {"messages": [{"id": "1"}], "nextPageToken": "page2"}
                )
            else:
                assert params["format"] == "raw"
                body = {"/1": one, "/2": two}[url[-2:]]
            response = Response()
            response.status_code = 200
            response._content = json.dumps(body).encode()
            return response

    credentials = Mock()
    monkeypatch.setattr(gmail_inbox.Credentials, "from_authorized_user_file", credentials)
    monkeypatch.setattr(gmail_inbox, "AuthorizedSession", lambda _: Session())
    items = list(gmail_inbox.gmail_items(tmp_path / "token", query, "desk"))
    credentials.assert_called_once_with(
        str(tmp_path / "token"), scopes=["https://www.googleapis.com/auth/gmail.readonly"]
    )
    assert len(requested) == 4
    assert [item.raw for item in items] == [mime_one, mime_two]
    path = tmp_path / "download.eml"
    path.write_bytes(mime_one)
    assert read_mail(path, "desk") == items[0]
    with desktop_turn(tmp_path / "desk") as db:
        assert len(collect(db, items)) == 2
        assert (
            collect(db, gmail_inbox.connector_gmail_items(StringIO(json.dumps([one, two])), "desk"))
            == []
        )


def test_incomplete_connector_original_rolls_back_partial_poll(tmp_path):
    one, _ = gmail_original(1)
    incomplete = {"id": "2", "raw": "invalid !"}
    with desktop_turn(tmp_path / "desk") as db:
        originals = gmail_inbox.connector_gmail_items(
            StringIO(json.dumps([one, incomplete])), "desk"
        )
        with pytest.raises(ValueError):
            collect(db, originals)
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0


def test_discovery_uses_receiving_day_and_requires_provider_receipt_metadata():
    scope = gmail_inbox.MailScope(None, "supplier@example.org", date(2026, 11, 19))
    start = int(datetime.fromisoformat("2026-11-19T00:00:00+09:00").timestamp())
    end = int(datetime.fromisoformat("2026-11-20T00:00:00+09:00").timestamp())
    assert scope.query() == f'in:inbox from:"supplier@example.org" after:{start - 1} before:{end}'
    message, _ = gmail_original(1)
    with pytest.raises(ValueError, match="internalDate and labelIds"):
        list(
            gmail_inbox.connector_gmail_items(StringIO(json.dumps([message])), "desk", scope=scope)
        )
