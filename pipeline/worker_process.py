"""Bounded owned OCR process tree, including Windows virtualenv launchers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import psutil


def run_worker(command: list[str], *, log_path: Path, timeout: float) -> int:
    """Avoid inherited pipes; kill only this worker's identified descendants."""
    with log_path.open("xb") as stream:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            owned = psutil.Process(process.pid)
            identity = owned.create_time()
        except psutil.NoSuchProcess:
            owned, identity = None, None
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            children = []
            if owned is not None:
                try:
                    if owned.is_running() and owned.create_time() == identity:
                        children = owned.children(recursive=True)
                except psutil.NoSuchProcess:
                    pass
            for child in reversed(children):
                try:
                    child.kill()  # psutil verifies PID reuse for signal operations.
                except psutil.NoSuchProcess:
                    pass
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if children:
                psutil.wait_procs(children, timeout=5)
            raise
