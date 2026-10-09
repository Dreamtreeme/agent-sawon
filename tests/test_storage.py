import os
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pytest
from pydantic import ValidationError
from sawon_interfaces import EventCost

from sawon_runtime.storage import (
    InvalidTransitionError,
    LogIntegrityError,
    RunLog,
    StateStore,
    atomic_write_json,
)


def _append_batch(case_dir: str, agent_id: str) -> None:
    log = RunLog(Path(case_dir), case_id="synthetic-case", agent_id=agent_id)
    for index in range(8):
        log.append("observe", payload={"index": index})


def _exit_writer(case_dir: str, partial: bool) -> None:
    log = RunLog(Path(case_dir), case_id="synthetic-case", agent_id="child")
    log.append("observe")
    if partial:
        with log.path.open("ab") as stream:
            stream.write(b'{"seq":')
            stream.flush()
            os.fsync(stream.fileno())
    os._exit(7)


@pytest.mark.parametrize("partial", [False, True])
def test_process_exit_keeps_complete_prefix_or_rejects_partial_tail(tmp_path, partial):
    child = get_context("spawn").Process(target=_exit_writer, args=(str(tmp_path), partial))
    child.start()
    child.join(timeout=20)
    if child.is_alive():
        child.kill()
        child.join()
        pytest.fail("writer did not exit")
    assert child.exitcode == 7
    restarted = RunLog(tmp_path, case_id="synthetic-case", agent_id="parent")
    prefix = restarted.path.read_bytes()
    if partial:
        with pytest.raises(LogIntegrityError):
            restarted.append("stop")
        assert restarted.path.read_bytes() == prefix
    else:
        assert restarted.append("stop").seq == 1
        assert restarted.path.read_bytes().startswith(prefix)


def test_log_restart_preserves_prefix_and_sequence(tmp_path):
    log = RunLog(tmp_path, case_id="synthetic-case", agent_id="vm1")
    assert log.append("observe", payload={"note": "합성 데이터"}).seq == 0
    prefix = log.path.read_bytes()
    restarted = RunLog(tmp_path, case_id="synthetic-case", agent_id="vm2")
    assert restarted.append("stop", payload={"reason": "test finished"}).seq == 1
    assert log.path.read_bytes().startswith(prefix)
    assert [e.agent_id for e in log.read_all()] == ["vm1", "vm2"]


def test_concurrent_processes_keep_all_events_and_unique_sequences(tmp_path):
    with ProcessPoolExecutor(max_workers=3, mp_context=get_context("spawn")) as pool:
        futures = [pool.submit(_append_batch, str(tmp_path), f"vm{n}") for n in range(3)]
        for future in futures:
            future.result(timeout=30)
    events = RunLog(tmp_path, case_id="synthetic-case", agent_id="reader").read_all()
    assert [e.seq for e in events] == list(range(24))
    assert len({(e.agent_id, e.payload["index"]) for e in events}) == 24


def test_corrupt_log_is_not_modified(tmp_path):
    # Incomplete tail and a complete blank JSON line reach the two distinct parser guards.
    for case, corruption in (("partial", b'{"seq":'), ("blank-line", b"\n")):
        log = RunLog(tmp_path / case, case_id="synthetic-case", agent_id="vm1")
        log.append("observe")
        with log.path.open("ab") as stream:
            stream.write(corruption)
        before = log.path.read_bytes()
        with pytest.raises(LogIntegrityError):
            log.append("stop")
        assert log.path.read_bytes() == before, case


def test_wrong_case_and_invalid_sequence_are_rejected(tmp_path):
    log = RunLog(tmp_path, case_id="synthetic-case", agent_id="vm1")
    event = log.append("observe")
    with pytest.raises(LogIntegrityError):
        RunLog(tmp_path, case_id="different-case", agent_id="vm1").append("observe")
    with log.path.open("ab") as stream:
        stream.write((event.model_dump_json() + "\n").encode())
    with pytest.raises(LogIntegrityError):
        log.read_all()


def test_invalid_event_does_not_append(tmp_path):
    log = RunLog(tmp_path, case_id="synthetic-case", agent_id="vm1")
    with pytest.raises(ValidationError):
        log.append("not-an-event")
    assert not log.path.exists()


def test_mutated_cost_cannot_poison_existing_log(tmp_path):
    log = RunLog(tmp_path, case_id="synthetic-case", agent_id="vm1")
    log.append("observe")
    before = log.path.read_bytes()
    cost = EventCost()
    cost.input_tokens = -1
    with pytest.raises(ValidationError):
        log.append("model_call", cost=cost)
    assert log.path.read_bytes() == before
    assert len(log.read_all()) == 1


@pytest.mark.parametrize("strict", [False, True])
def test_append_validation_cost_and_interleaved_writers(tmp_path, monkeypatch, strict):
    log = RunLog(tmp_path, case_id="synthetic-case", agent_id="vm1", verify_each_append=strict)
    peer = RunLog(tmp_path, case_id="synthetic-case", agent_id="vm2")
    original = Path.read_bytes
    reads = []

    def measured_read(path):
        data = original(path)
        if path == log.path:
            reads.append(len(data))
        return data

    monkeypatch.setattr(Path, "read_bytes", measured_read)
    for _ in range(20):
        log.append("observe")
    assert len(reads) == (19 if strict else 0)
    prefix = original(log.path)
    assert peer.append("observe").seq == 20
    assert log.append("stop").seq == 21
    assert original(log.path).startswith(prefix)
    events = log.read_all()
    assert [event.seq for event in events] == list(range(22))
    assert events[20].agent_id == "vm2"


@pytest.mark.parametrize("change", ["middle", "replace", "truncate"])
def test_cached_append_revalidates_changed_file(tmp_path, change):
    log = RunLog(tmp_path, case_id="synthetic-case", agent_id="vm1")
    for _ in range(3):
        log.append("observe")
    raw = log.path.read_bytes()
    corrupted = raw.replace(b'"seq":1', b'"seq":9', 1)
    if change == "replace":
        replacement = tmp_path / "replacement.jsonl"
        replacement.write_bytes(corrupted)
        stat = log.path.stat()
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        os.replace(replacement, log.path)
    elif change == "truncate":
        log.path.write_bytes(raw[:-1])
    else:
        log.path.write_bytes(corrupted)
    before = log.path.read_bytes()
    with pytest.raises(LogIntegrityError):
        log.append("stop")
    with pytest.raises(LogIntegrityError):
        log.read_all()
    assert log.path.read_bytes() == before


def test_failed_fsync_revalidates_committed_bytes_before_retry(tmp_path, monkeypatch):
    from sawon_runtime import storage

    log = RunLog(tmp_path, case_id="synthetic-case", agent_id="vm1")
    log.append("observe")
    with monkeypatch.context() as patch:

        def fail_fsync(descriptor):
            raise OSError("injected disk sync failure")

        patch.setattr(storage.os, "fsync", fail_fsync)
        with pytest.raises(OSError, match="disk sync"):
            log.append("observe")
    assert log.append("stop").seq == 2
    assert [event.seq for event in log.read_all()] == [0, 1, 2]


def test_state_transitions_and_stale_updates(tmp_path):
    store = StateStore(tmp_path, case_id="synthetic-case")
    assert store.initialize().status == "received"
    assert store.transition("ready", expected="received").status == "ready"
    assert store.initialize().status == "ready"
    before = store.path.read_bytes()
    with pytest.raises(InvalidTransitionError, match="Expected"):
        store.transition("entering", expected="received")
    with pytest.raises(InvalidTransitionError, match="Forbidden"):
        store.transition("approved", expected="ready")
    assert store.path.read_bytes() == before
    store.transition("entering", expected="ready")
    store.transition("awaiting_approval", expected="entering")
    store.transition("rejected", expected="awaiting_approval", reason="manual correction")
    store.transition("ready", expected="rejected")


def test_failed_state_is_terminal(tmp_path):
    store = StateStore(tmp_path, case_id="synthetic-case")
    store.initialize()
    store.transition("failed", expected="received", reason="invalid record")
    with pytest.raises(InvalidTransitionError):
        store.transition("ready", expected="failed")


def test_state_cannot_be_read_by_another_case(tmp_path):
    StateStore(tmp_path, case_id="synthetic-case").initialize()
    with pytest.raises(InvalidTransitionError):
        StateStore(tmp_path, case_id="another-case").read()


def test_missing_status_is_rejected_without_replacing_evidence(tmp_path):
    store = StateStore(tmp_path, case_id="synthetic-case")
    atomic_write_json(
        store.path,
        {"case_id": "synthetic-case", "updated_at": "2026-09-28T06:00:00+00:00"},
    )
    before = store.path.read_bytes()
    with pytest.raises(ValidationError):
        store.read()
    with pytest.raises(ValidationError):
        store.transition("ready", expected="received")
    assert store.path.read_bytes() == before
