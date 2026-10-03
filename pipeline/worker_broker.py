"""Authenticated loopback broker retaining local OCR/detector workers across tasks."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .persistent_worker import PersistentWorker

MAX_REQUEST_BYTES = 1_000_000


class WorkerBroker(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, token: str, workers: dict[str, PersistentWorker]):
        super().__init__(address, WorkerBrokerHandler)
        self.token = token
        self.workers = workers

    def close(self) -> None:
        for worker in self.workers.values():
            worker.close()
        self.server_close()


class WorkerBrokerHandler(BaseHTTPRequestHandler):
    server: WorkerBroker

    def log_message(self, format, *args):
        return

    def _authorized(self) -> bool:
        value = self.headers.get("Authorization", "")
        return hmac.compare_digest(value, "Bearer " + self.server.token)

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._authorized():
            self._reply(401, {"ok": False})
        elif self.path == "/health":
            self._reply(200, {"ok": True, "workers": sorted(self.server.workers)})
        else:
            self._reply(404, {"ok": False})

    def do_POST(self) -> None:
        if not self._authorized():
            self._reply(401, {"ok": False})
            return
        if self.path == "/shutdown":
            self._reply(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        prefix = "/request/"
        key = self.path.removeprefix(prefix) if self.path.startswith(prefix) else ""
        worker = self.server.workers.get(key)
        length = int(self.headers.get("Content-Length", "0"))
        if worker is None or not 0 < length <= MAX_REQUEST_BYTES:
            self._reply(400, {"ok": False})
            return
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            payload, timeout = request["payload"], request["timeout"]
            if not isinstance(payload, dict) or not isinstance(timeout, (int, float)):
                raise ValueError
            if not 0 < timeout <= 1800:
                raise ValueError
            result = worker.request(payload, timeout=float(timeout))
        except Exception:
            self._reply(503, {"ok": False})
            return
        self._reply(200, {"ok": True, "result": result, "metrics": worker.last_metrics})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["127.0.0.1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--ocr-python", required=True)
    parser.add_argument("--detector-python", required=True)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be valid")
    token = os.environ.get("SAWON_LOCAL_WORKER_TOKEN")
    if not token:
        parser.error("SAWON_LOCAL_WORKER_TOKEN is required")
    runtime = args.runtime_dir.resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    workers = {
        "ocr": PersistentWorker(
            [args.ocr_python, str(root / "pipeline/ocr_worker.py"), "--serve"],
            directory=runtime / "ocr",
        ),
        "detector": PersistentWorker(
            [
                args.detector_python,
                str(root / "brain/visual/detector_worker.py"),
                "--serve",
                str(runtime / "yolo"),
            ],
            directory=runtime / "detector",
        ),
    }
    server = WorkerBroker((args.host, args.port), token, workers)
    try:
        server.serve_forever()
    finally:
        server.close()


if __name__ == "__main__":
    main()
