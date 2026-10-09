"""Shared OCR/detector process cleanup, including Windows virtualenv launchers."""

from __future__ import annotations

import psutil


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
