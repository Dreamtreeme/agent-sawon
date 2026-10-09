import errno
import json
import os
from pathlib import Path

import pytest
from case_labels import labeled_case

from sawon_runtime import storage


def _windows_error(code: int) -> OSError:
    error = PermissionError(errno.EACCES, "simulated Windows replacement failure")
    # Assignment also works on POSIX, keeping these tests independent of the host OS.
    error.winerror = code
    return error


def test_transient_windows_replace_retries_every_allowlisted_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_replace = storage.os.replace
    for winerror in (5, 32, 33):
        with labeled_case(f"winerror={winerror}"):
            path = tmp_path / str(winerror) / "status.json"
            storage.atomic_write_json(path, {"status": "received"})
            before = path.read_bytes()
            sources: list[Path] = []
            sleeps: list[float] = []

            def intermittent_replace(
                source: str,
                destination: Path,
                *,
                current_sources=sources,
                current_path=path,
                original=before,
                code=winerror,
            ) -> None:
                current_sources.append(Path(source))
                assert destination == current_path
                assert current_path.read_bytes() == original
                assert json.loads(Path(source).read_text(encoding="utf-8")) == {"status": "ready"}
                if len(current_sources) < 3:
                    raise _windows_error(code)
                original_replace(source, destination)

            with monkeypatch.context() as patch:
                patch.setattr(storage.os, "replace", intermittent_replace)
                patch.setattr(storage.time, "sleep", sleeps.append)
                storage.atomic_write_json(path, {"status": "ready"})

            assert json.loads(path.read_text(encoding="utf-8")) == {"status": "ready"}
            assert len(sources) == 3 and len(set(sources)) == 1
            assert sleeps == [0.025, 0.05]
            assert list(path.parent.glob("*.tmp")) == []


def test_exhausted_windows_replace_preserves_original_for_every_allowlisted_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for winerror in (5, 32, 33):
        with labeled_case(f"winerror={winerror}"):
            path = tmp_path / str(winerror) / "status.json"
            storage.atomic_write_json(path, {"status": "received"})
            before = path.read_bytes()
            failure = _windows_error(winerror)
            attempts = 0
            sleeps: list[float] = []

            def locked_replace(
                source: str,
                destination: Path,
                *,
                current_path=path,
                original=before,
                error=failure,
            ) -> None:
                nonlocal attempts
                attempts += 1
                assert current_path.read_bytes() == original
                assert json.loads(Path(source).read_text(encoding="utf-8")) == {"status": "ready"}
                raise error

            with monkeypatch.context() as patch:
                patch.setattr(storage.os, "replace", locked_replace)
                patch.setattr(storage.time, "sleep", sleeps.append)
                with pytest.raises(OSError) as caught:
                    storage.atomic_write_json(path, {"status": "ready"})

            assert caught.value is failure
            assert attempts == 6 and len(sleeps) == 5
            assert sum(sleeps) == pytest.approx(0.775)
            assert path.read_bytes() == before
            assert list(path.parent.glob("*.tmp")) == []


def test_unrelated_replace_error_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures = (
        ("posix-access", PermissionError(errno.EACCES, "POSIX access denied")),
        ("disk-full", OSError(errno.ENOSPC, "disk full")),
        ("invalid-parameter", _windows_error(87)),
    )
    for case, failure in failures:
        with labeled_case(case):
            path = tmp_path / case / "status.json"
            storage.atomic_write_json(path, {"status": "received"})
            before = path.read_bytes()
            attempts = 0
            sleeps: list[float] = []

            def failed_replace(source: str, destination: Path, *, error=failure) -> None:
                nonlocal attempts
                attempts += 1
                raise error

            with monkeypatch.context() as patch:
                patch.setattr(storage.os, "replace", failed_replace)
                patch.setattr(storage.time, "sleep", sleeps.append)
                with pytest.raises(OSError) as caught:
                    storage.atomic_write_json(path, {"status": "ready"})

            assert caught.value is failure
            assert attempts == 1 and sleeps == []
            assert path.read_bytes() == before
            assert list(path.parent.glob("*.tmp")) == []


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
