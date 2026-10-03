"""One owned local process for multiple images; no model or image result cache."""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

import psutil

from .worker_process import stop_worker_tree, worker_children


class WorkerError(RuntimeError):
    pass


class PersistentWorker:
    def __init__(self, command: list[str], *, directory: Path, shared_key: str | None = None):
        self.command, self.directory = command, directory.resolve()
        self.shared_key = shared_key
        self._shared_url = os.environ.get("SAWON_LOCAL_WORKER_URL") if shared_key else None
        self._shared_token = os.environ.get("SAWON_LOCAL_WORKER_TOKEN") if shared_key else None
        if self._shared_url and not self._shared_token:
            raise WorkerError("shared_local_worker_token_missing")
        self.process: subprocess.Popen[str] | None = None
        self._owned: psutil.Process | None = None
        self._identity: float | None = None
        self._folder: tempfile.TemporaryDirectory[str] | None = None
        self._stream: BinaryIO | None = None
        self._lock = threading.RLock()
        self.last_metrics: dict[str, Any] = {}

    def _start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        self.close()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._folder = tempfile.TemporaryDirectory(prefix=".worker-", dir=self.directory)
        self._stream = (Path(self._folder.name) / "worker.log").open("xb")
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=self._stream,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._owned = psutil.Process(self.process.pid)
            self._identity = self._owned.create_time()
            atexit.register(self.close)
        except BaseException:
            self.close()
            raise

    def request(self, payload: dict[str, Any], *, timeout: float) -> Any:
        with self._lock:
            self.last_metrics = {}
            if self._shared_url is not None:
                return self._shared_request(payload, timeout=timeout)
            started = time.perf_counter()
            try:
                self._start()
                request_id = uuid4().hex
                folder = Path(self._folder.name)
                source, output = (
                    folder / f"{request_id}.request.json",
                    folder / f"{request_id}.json",
                )
                source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                self.process.stdin.write(
                    json.dumps(
                        {
                            "request_id": request_id,
                            "request": str(source),
                            "output": str(output),
                        }
                    )
                    + "\n"
                )
                self.process.stdin.flush()
                while True:
                    try:
                        reply = json.loads(output.read_text(encoding="utf-8"))
                        break
                    except FileNotFoundError:
                        pass  # The worker has not published its atomic response yet.
                    except PermissionError:
                        if sys.platform != "win32":
                            raise
                        # Windows can expose the renamed response before allowing an
                        # open. Wait for this same response; do not restart inference.
                    if self.process.poll() is not None:
                        raise WorkerError("local_worker_exited")
                    if time.perf_counter() - started >= timeout:
                        raise subprocess.TimeoutExpired(self.command, timeout)
                    time.sleep(0.02)
                if reply.get("request_id") != request_id or not reply.get("ok"):
                    raise WorkerError("local_worker_request_failed")
                self.last_metrics = {
                    **reply["metrics"],
                    "round_trip_ms": (time.perf_counter() - started) * 1000,
                }
                source.unlink()
                output.unlink()
                return reply["result"]
            except BaseException:
                self.close()
                raise

    def _shared_request(self, payload: dict[str, Any], *, timeout: float) -> Any:
        started = time.perf_counter()
        request = urllib.request.Request(
            self._shared_url.rstrip("/") + "/request/" + self.shared_key,
            data=json.dumps({"payload": payload, "timeout": timeout}).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self._shared_token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout + 5) as reply:
                response = json.loads(reply.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise WorkerError("shared_local_worker_request_failed") from exc
        if not response.get("ok"):
            raise WorkerError("shared_local_worker_request_failed")
        self.last_metrics = {
            **response.get("metrics", {}),
            "shared": True,
            "round_trip_ms": (time.perf_counter() - started) * 1000,
        }
        return response["result"]

    def close(self) -> None:
        with self._lock:
            atexit.unregister(self.close)
            if self.process is not None:
                children = worker_children(self._owned, self._identity)
                try:
                    if self.process.stdin:
                        self.process.stdin.close()
                    self.process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                finally:
                    stop_worker_tree(self.process, self._owned, self._identity, children=children)
                    self.process = self._owned = self._identity = None
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            if self._folder is not None:
                target = Path(self._folder.name).resolve()
                if target.parent != self.directory or not target.name.startswith(".worker-"):
                    raise WorkerError("worker_cleanup_path_mismatch")
                self._folder.cleanup()
                self._folder = None
