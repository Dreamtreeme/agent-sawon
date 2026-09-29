from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pytest
from pydantic import ValidationError
from sawon_interfaces import EventCost

from brain.runtime import (
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


@pytest.mark.parametrize("corruption", [b'{"seq":', b"not-json\n", b"\n"])
def test_corrupt_log_is_not_modified(tmp_path, corruption):
    log = RunLog(tmp_path, case_id="synthetic-case", agent_id="vm1")
    log.append("observe")
    with log.path.open("ab") as stream:
        stream.write(corruption)
    before = log.path.read_bytes()
    with pytest.raises(LogIntegrityError):
        log.append("stop")
    assert log.path.read_bytes() == before


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
