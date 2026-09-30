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
            stop_worker_tree(process, owned, identity)
            raise


def worker_children(owned, identity) -> list:
    if owned is not None:
        try:
            if owned.is_running() and owned.create_time() == identity:
                return owned.children(recursive=True)
        except psutil.NoSuchProcess:
            pass
    return []


def stop_worker_tree(process, owned, identity, *, children=None) -> None:
    """Stop only the matching process and its identified descendants."""
    if children is None:
        children = worker_children(owned, identity)
    for child in reversed(children):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)
    if children:
        psutil.wait_procs(children, timeout=5)
