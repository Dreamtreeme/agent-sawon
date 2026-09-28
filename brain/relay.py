"""The brain's only environment boundary: authenticated relay HTTP."""

from __future__ import annotations

import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sawon_interfaces import Action, ActionResult, Observation


class RelayError(RuntimeError):
    pass


class Relay(Protocol):
    def health(self) -> dict: ...
    def observe(self) -> Observation: ...
    def act(self, action: Action) -> ActionResult: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirect must not forward the bearer credential to another service.
        return None


class HttpRelay:
    def __init__(self, url: str, token: str, *, timeout: float = 30, max_depth: int = 12) -> None:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
        ):
            raise ValueError("Relay URL must be an HTTP(S) origin without credentials")
        if not token or any(char in token for char in "\r\n"):
            raise ValueError("SAWON_RELAY_TOKEN is required")
        self.url = url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self.max_depth = max_depth
        self._opener = build_opener(_NoRedirect)

    def _request(self, path: str, body: dict | None = None) -> dict:
        request = Request(
            self.url + path,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="GET" if body is None else "POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                data = response.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise RelayError("relay_response_too_large")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise RelayError("relay_response_not_object")
            return result
        except HTTPError as exc:
            # Server bodies can contain UI text or credentials; do not echo them.
            raise RelayError(f"relay_http_{exc.code}") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise RelayError("relay_transport_or_json_error") from None

    def health(self) -> dict:
        return self._request("/health")

    def observe(self) -> Observation:
        query = urlencode({"screenshot": "false", "max_depth": self.max_depth})
        return Observation.model_validate(self._request(f"/observe?{query}"))

    def act(self, action: Action) -> ActionResult:
        return ActionResult.model_validate(self._request("/act", action.model_dump(mode="json")))
