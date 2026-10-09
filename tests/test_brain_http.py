"""Local HTTP fixtures exercise transport authentication without a model API."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import pytest
from case_labels import labeled_case
from sawon_interfaces import Action, ActionBatch

from brain.relay import HttpRelay, RelayError


@contextmanager
def server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/health")
                self.end_headers()
                return
            if self.path == "/health":
                value = {"ok": True}
            elif self.path.startswith("/observe?"):
                value = {
                    "obs_id": str(uuid4()),
                    "ts": datetime.now(UTC).isoformat(),
                    "active_window": None,
                    "windows": [],
                    "elements": [],
                    "screen_hash": "0" * 40,
                    "blocked_reason": "no_foreground",
                }
            else:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"secret response body")
                return
            encoded = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), body))
            self.send_response(200)
            self.end_headers()
            if self.path == "/act-batch":
                post_obs_id = str(uuid4())
                self.wfile.write(
                    json.dumps(
                        {
                            "batch_id": body["batch_id"],
                            "ok": True,
                            "results": [
                                {
                                    "action_id": item["action_id"],
                                    "ok": True,
                                    "post_obs_id": post_obs_id,
                                    "duration_ms": 0.0,
                                }
                                for item in body["actions"]
                            ],
                            "post_obs_id": post_obs_id,
                            "duration_ms": 0.0,
                        }
                    ).encode()
                )
                return
            self.wfile.write(
                json.dumps(
                    {
                        "action_id": body["action_id"],
                        "ok": True,
                        "post_obs_id": str(uuid4()),
                        "duration_ms": 0.0,
                    }
                ).encode()
            )

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_port}", requests
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=2)


def test_authenticated_health_observe_and_act():
    with server() as (url, requests):
        relay = HttpRelay(url, "fixture-token", max_depth=12)
        assert relay.health() == {"ok": True}
        observation = relay.observe()
        assert observation.blocked_reason == "no_foreground"
        action = Action.model_validate(
            {
                "type": "wait",
                "action_id": str(uuid4()),
                "obs_id": str(observation.obs_id),
                "reason": "fixture",
                "duration_ms": 0,
            }
        )
        assert relay.act(action).action_id == action.root.action_id
        assert all(item[1] == "Bearer fixture-token" for item in requests)
        assert requests[1][0] == "/observe?screenshot=false&max_depth=12"
        assert requests[2][2] == action.model_dump(mode="json")


def test_authenticated_direct_fill_batch():
    with server() as (url, requests):
        relay = HttpRelay(url, "fixture-token")
        obs_id, capture_id = uuid4(), uuid4()
        actions = [
            Action.model_validate(
                {
                    "schema_version": "0.2",
                    "type": "set_text",
                    "action_id": str(uuid4()),
                    "obs_id": str(obs_id),
                    "reason": "fill",
                    "text": str(index),
                    "target": {
                        "image_point": [index * 10, 10],
                        "capture_id": str(capture_id),
                        "evidence": "fixture",
                    },
                }
            )
            for index in (1, 2)
        ]
        batch = ActionBatch(batch_id=uuid4(), actions=actions)
        result = relay.act_batch(batch)
        assert result.ok and result.batch_id == batch.batch_id
        assert requests == [
            (
                "/act-batch",
                "Bearer fixture-token",
                batch.model_dump(mode="json"),
            )
        ]


def test_http_error_never_exposes_body_or_authentication_token():
    with server() as (url, requests):
        relay = HttpRelay(url, "fixture-token")
        for path, status in (("/denied", 403), ("/redirect", 302)):
            with labeled_case(f"{path}:{status}"):
                with pytest.raises(RelayError, match=f"^relay_http_{status}$"):
                    relay._request(path)
        assert requests == [
            ("/denied", "Bearer fixture-token"),
            ("/redirect", "Bearer fixture-token"),
        ]


def test_token_required_without_header_injection():
    for token in ("", "invalid\r\nheader"):
        with labeled_case(repr(token)):
            with pytest.raises(ValueError):
                HttpRelay("http://localhost:1234", token)
