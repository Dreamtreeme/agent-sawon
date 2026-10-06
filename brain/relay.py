"""The brain's only environment boundary: authenticated relay HTTP."""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sawon_interfaces import Action, ActionBatch, ActionBatchResult, ActionResult, Observation


class RelayError(RuntimeError):
    pass


class Relay(Protocol):
    def health(self) -> dict: ...
    def observe(self) -> Observation: ...
    def act(self, action: Action) -> ActionResult: ...
    def act_batch(self, batch: ActionBatch) -> ActionBatchResult: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirect must not forward the bearer credential to another service.
        return None


class HttpRelay:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: float = 30,
        max_depth: int = 12,
        visual: bool = False,
    ) -> None:
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
        self.visual = visual
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
        health = self._request("/health")
        mode = health.get("observation_mode")
        # visual 클라이언트가 아닌데 서버가 visual/hybrid 모드인 경우 차단
        if mode in {"visual_only", "hybrid"} and not self.visual:
            raise RelayError("visual_mode_not_enabled")
        # visual 클라이언트인데 서버가 visual/hybrid 모드가 아닌 경우 차단
        if self.visual and mode not in {"visual_only", "hybrid"}:
            raise RelayError("visual_relay_mode_mismatch")
        return health

    def observe(self) -> Observation:
        query = urlencode({"screenshot": "false", "max_depth": self.max_depth})
        observation = Observation.model_validate(self._request(f"/observe?{query}"))
        mode = observation.observation_mode
        if mode in {"visual_only", "hybrid"} and not self.visual:
            raise RelayError("visual_mode_not_enabled")
        if self.visual and mode not in {"visual_only", "hybrid"}:
            raise RelayError("visual_relay_mode_mismatch")
        return observation

    def screenshot(self, observation: Observation, destination: Path) -> Path:
        """Fetch only a relay-owned PNG; redirects and arbitrary origins are denied."""
        from PIL import Image

        shot = observation.screenshot
        if not self.visual or shot is None or shot.geometry is None:
            raise RelayError("visual_capture_missing")
        if not re.fullmatch(r"screenshots/[A-Za-z0-9_.-]+\.png", shot.path):
            raise RelayError("invalid_screenshot_path")
        request = Request(
            self.url + "/" + shot.path,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "image/png",
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                data = response.read(32 * 1024 * 1024 + 1)
        except (HTTPError, URLError, TimeoutError, OSError):
            raise RelayError("screenshot_transport_error") from None
        if (
            len(data) > 32 * 1024 * 1024
            or hashlib.sha1(data).hexdigest() != observation.screen_hash
        ):
            raise RelayError("screenshot_integrity_error")
        with Image.open(io.BytesIO(data)) as picture:
            if picture.format != "PNG" or picture.size != (shot.width, shot.height):
                raise RelayError("screenshot_geometry_mismatch")
            if shot.width * shot.height > 40_000_000:
                raise RelayError("screenshot_pixel_limit")
            picture.verify()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(data)
        return destination

    def act(self, action: Action) -> ActionResult:
        return ActionResult.model_validate(self._request("/act", action.model_dump(mode="json")))

    def act_batch(self, batch: ActionBatch) -> ActionBatchResult:
        return ActionBatchResult.model_validate(
            self._request("/act-batch", batch.model_dump(mode="json"))
        )
