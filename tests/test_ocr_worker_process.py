"""A timed-out Windows venv launcher must not leave native OCR children alive."""

import subprocess
import sys

import psutil
import pytest

from pipeline.worker_process import run_worker


def test_worker_timeout_kills_owned_descendant(tmp_path):
    pid_path = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],"
        "creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)); "
        "Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)"
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_worker(
                [sys.executable, "-c", code, str(pid_path)],
                log_path=tmp_path / "worker.log",
                timeout=2,
            )
        assert pid_path.exists()
        pid = int(pid_path.read_text())
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    finally:
        if pid_path.exists():
            pid = int(pid_path.read_text())
            if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                psutil.Process(pid).kill()


def test_successful_worker_returns_without_shell_or_visible_console(tmp_path):
    assert (
        run_worker(
            [sys.executable, "-c", "print('worker completed')"],
            log_path=tmp_path / "worker.log",
            timeout=5,
        )
        == 0
    )
    assert "worker completed" in (tmp_path / "worker.log").read_text()
