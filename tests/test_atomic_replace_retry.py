import errno
import json
import os
from pathlib import Path

import pytest

from sawon_runtime import storage


def _windows_error(code: int) -> OSError:
    error = PermissionError(errno.EACCES, "simulated Windows replacement failure")
    # Assignment also works on POSIX, keeping these tests independent of the host OS.
    error.winerror = code
    return error


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_transient_windows_replace_retries_same_complete_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int
) -> None:
    path = tmp_path / "status.json"
    storage.atomic_write_json(path, {"status": "received"})
    before = path.read_bytes()
    original_replace = storage.os.replace
    sources: list[Path] = []
    sleeps: list[float] = []

    def intermittent_replace(source: str, destination: Path) -> None:
        sources.append(Path(source))
        assert destination == path
        assert path.read_bytes() == before
        assert json.loads(Path(source).read_text(encoding="utf-8")) == {"status": "ready"}
        if len(sources) < 3:
            raise _windows_error(winerror)
        original_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", intermittent_replace)
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)

    storage.atomic_write_json(path, {"status": "ready"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "ready"}
    assert len(sources) == 3
    assert len(set(sources)) == 1
    assert sleeps == [0.025, 0.05]
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_exhausted_windows_replace_preserves_original_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int
) -> None:
    path = tmp_path / "status.json"
    storage.atomic_write_json(path, {"status": "received"})
    before = path.read_bytes()
    failure = _windows_error(winerror)
    attempts = 0
    sleeps: list[float] = []

    def locked_replace(source: str, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        assert path.read_bytes() == before
        assert json.loads(Path(source).read_text(encoding="utf-8")) == {"status": "ready"}
        raise failure

    monkeypatch.setattr(storage.os, "replace", locked_replace)
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)

    with pytest.raises(OSError) as caught:
        storage.atomic_write_json(path, {"status": "ready"})

    assert caught.value is failure
    assert attempts == 6
    assert len(sleeps) == 5
    assert sum(sleeps) == pytest.approx(0.775)
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "failure",
    [
        PermissionError(errno.EACCES, "POSIX access denied"),
        OSError(errno.ENOSPC, "disk full"),
        _windows_error(87),
    ],
)
def test_unrelated_replace_error_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: OSError
) -> None:
    path = tmp_path / "status.json"
    storage.atomic_write_json(path, {"status": "received"})
    before = path.read_bytes()
    attempts = 0
    sleeps: list[float] = []

    def failed_replace(source: str, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise failure

    monkeypatch.setattr(storage.os, "replace", failed_replace)
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)

    with pytest.raises(OSError) as caught:
        storage.atomic_write_json(path, {"status": "ready"})

    assert caught.value is failure
    assert attempts == 1
    assert sleeps == []
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_successful_replace_never_sleeps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(storage.time, "sleep", sleeps.append)
    path = tmp_path / "status.json"

    storage.atomic_write_json(path, {"status": "ready"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "ready"}
    assert sleeps == []
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows denies replacement of this open reader")
def test_real_windows_reader_can_close_during_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "status.json"
    storage.atomic_write_json(path, {"status": "received"})
    before = path.read_bytes()
    sleeps: list[float] = []

    with path.open("rb") as reader:

        def close_reader(delay: float) -> None:
            sleeps.append(delay)
            assert reader.read() == before
            reader.close()

        monkeypatch.setattr(storage.time, "sleep", close_reader)
        storage.atomic_write_json(path, {"status": "ready"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "ready"}
    assert sleeps == [0.025]
    assert list(tmp_path.glob("*.tmp")) == []
