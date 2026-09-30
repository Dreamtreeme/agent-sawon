"""Stdlib-only request loop shared by isolated OCR and detector environments."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from time import perf_counter


def serve(handler) -> None:
    # stdout/stderr belong to the private worker log, not the control protocol.
    for line in sys.stdin:
        command = json.loads(line)
        destination = Path(command["output"])
        started = perf_counter()
        try:
            request = json.loads(Path(command["request"]).read_text(encoding="utf-8"))
            result = handler(request)
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error_type": type(exc).__name__}
        response.update(
            request_id=command["request_id"],
            metrics={
                **getattr(handler, "metrics", {}),
                "pid": os.getpid(),
                "request_ms": (perf_counter() - started) * 1000,
            },
        )
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
        temporary.replace(destination)
        if not response["ok"]:
            return  # A failed native model is never reused for the next request.
