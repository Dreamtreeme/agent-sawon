"""Cache proofs survive fresh refs, while ambiguity and stale evidence miss."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sawon_interfaces import Observation, ShipmentRecord, UIElement, WindowInfo

from brain.cache import CacheIntegrityError, SemanticCache
from brain.graph.schemas import Choice, Decision


@pytest.fixture
def record():
    values = {
        "hbl_no": "HBL-EXAMPLE-X",
        "mbl_no": "MBL-EXAMPLE-Y",
        "shipper": "Example Supply",
        "consignee": "Example Import",
        "description": "Synthetic machinery",
        "packages": {"count": 12, "unit": "CT"},
        "gross_weight_kg": 1200.5,
        "container_no": "EXAMPLE-CONTAINER",
        "pol": "AAAAB",
        "pod": "BBBBA",
    }
    return ShipmentRecord.model_validate(
        {
            "case_id": "cache-fixture",
            "origin": "manual",
            "fields": {key: {"value": value} for key, value in values.items()},
        }
    )


def observation(
    value="",
    *,
    shift=0,
    seconds=0,
    label="Shipment identifier",
    focused=True,
    duplicate=False,
    window="Editor",
):
    win = WindowInfo(handle=7, title=window, process="fixture.exe")
    parent = UIElement(ref=f"e{shift}", role="Window", name=window, bbox=(50.0, 10.0, 500.0, 400.0))
    child = UIElement(
        ref=f"e{shift + 1}",
        role="Edit",
        name=label,
        value=value,
        focused=focused,
        bbox=(125.0, 15.0, 99.0, 42.0),
        parent_ref=parent.ref,
        automation_id="unstable-identifier",
    )
    children = [child]
    if duplicate:
        children.append(child.model_copy(update={"ref": f"e{shift + 2}"}))
    return Observation(
        obs_id=uuid4(),
        ts=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds),
        active_window=win,
        windows=[win],
        elements=[parent, *children],
        screen_hash="1" * 40,
    )


def fill(ref="e1", source="hbl_no"):
    return Decision(
        screen_kind="editor",
        operation="act",
        intent="fill",
        subgoal="enter",
        reason="Current observed input",
        action=Choice(type="set_text", ref=ref, source=source),
    )


def seed(cache, record):
    session = cache.start_run()
    before, after = observation(), observation(record.fields.hbl_no.value, shift=20, seconds=1)
    assert session.stage_validated(before, after, fill(), record)
    assert session.finish(success=True) == 1
    return cache.entries()[0]


def test_promotes_only_after_full_success(tmp_path, record):
    cache = SemanticCache(tmp_path / "cache.json")
    session = cache.start_run()
    assert session.stage_validated(
        observation(), observation(record.fields.hbl_no.value, seconds=1), fill(), record
    )
    assert not cache.path.exists()
    assert session.finish(success=False) == 0
    assert cache.entries() == ()
    seed(cache, record)
    assert len(cache.entries()) == 1


def test_current_ref_and_reordered_elements_are_resolved_again(tmp_path, record):
    cache = SemanticCache(tmp_path / "cache.json")
    seed(cache, record)
    current = observation(shift=300, seconds=2)
    current = current.model_copy(update={"elements": list(reversed(current.elements))})
    hit = cache.start_run().lookup(
        current, phase="enter", allowed_sources=["hbl_no"], record=record
    )
    assert hit is not None
    assert hit.obs_id == current.obs_id
    assert hit.decision.action.ref == "e301"
    assert hit.decision.action.source == "hbl_no"
    serialized = cache.path.read_text(encoding="utf-8")
    for forbidden in [
        "e301",
        "e21",
        "automation_id",
        "unstable-identifier",
        "bbox",
        "parent_ref",
        '"handle"',
        record.fields.hbl_no.value,
    ]:
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "options", [{"duplicate": True}, {"label": "Changed label"}, {"window": "Another editor"}]
)
def test_ambiguous_or_changed_semantics_miss(tmp_path, record, options):
    cache = SemanticCache(tmp_path / "cache.json")
    seed(cache, record)
    assert (
        cache.start_run().lookup(observation(**options), phase="enter", allowed_sources=["hbl_no"])
        is None
    )


def test_disabled_cache_does_not_read_corrupt_file(tmp_path, record):
    path = tmp_path / "cache.json"
    path.write_text("corrupt", encoding="utf-8")
    session = SemanticCache(path, enabled=False).start_run()
    assert session.lookup(observation(), phase="enter", allowed_sources=["hbl_no"]) is None
    assert not session.stage_validated(
        observation(), observation(record.fields.hbl_no.value, seconds=1), fill(), record
    )
    assert session.finish(success=True) == 0
    assert path.read_text() == "corrupt"


def test_retry_then_invalidation_and_successful_relearning(tmp_path, record):
    cache = SemanticCache(tmp_path / "cache.json", max_retries=1)
    entry = seed(cache, record)
    session = cache.start_run()
    assert not session.record_failure(entry.entry_id)
    assert session.lookup(observation(), phase="enter", allowed_sources=["hbl_no"]) is not None
    assert session.record_failure(entry.entry_id)
    assert session.lookup(observation(), phase="enter", allowed_sources=["hbl_no"]) is None
    seed(cache, record)
    assert cache.entries()[0].failure_count == 0
    assert cache.entries()[0].success_count == 2


@pytest.mark.parametrize(
    "mode",
    [
        "same_observation",
        "older",
        "wrong_value",
        "duplicate",
        "protected",
        "data_in_label",
        "window_changed",
        "already_correct",
        "login",
    ],
)
def test_unproven_actions_never_stage(tmp_path, record, mode):
    before = observation()
    after = observation(record.fields.hbl_no.value, seconds=1)
    decision = fill()
    if mode == "same_observation":
        after = after.model_copy(update={"obs_id": before.obs_id})
    elif mode == "older":
        after = after.model_copy(update={"ts": before.ts})
    elif mode == "wrong_value":
        after = observation("wrong", seconds=1)
    elif mode == "duplicate":
        after = observation(record.fields.hbl_no.value, seconds=1, duplicate=True)
    elif mode == "protected":
        before = observation(label="Password")
        after = observation(record.fields.hbl_no.value, seconds=1, label="Password")
    elif mode == "data_in_label":
        before = observation(label=record.fields.hbl_no.value)
        after = observation(record.fields.hbl_no.value, seconds=1, label=record.fields.hbl_no.value)
    elif mode == "window_changed":
        after = observation(record.fields.hbl_no.value, seconds=1, window="Other")
    elif mode == "already_correct":
        before = observation(record.fields.hbl_no.value)
    elif mode == "login":
        decision = decision.model_copy(update={"screen_kind": "login", "intent": "credential"})
    session = SemanticCache(tmp_path / "cache.json").start_run()
    assert not session.stage_validated(before, after, decision, record)
    assert session.finish(success=True) == 0


def test_corrupt_cache_preserved(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text('{"schema_version":"999"}', encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(CacheIntegrityError):
        SemanticCache(path).entries()
    assert path.read_bytes() == before


def test_conflicting_source_mappings_are_miss(tmp_path, record):
    cache = SemanticCache(tmp_path / "cache.json")
    seed(cache, record)
    session = cache.start_run()
    assert session.stage_validated(
        observation(),
        observation(record.fields.mbl_no.value, seconds=1),
        fill(source="mbl_no"),
        record,
    )
    session.finish(success=True)
    lookup = cache.start_run()
    assert lookup.lookup(observation(), phase="enter", allowed_sources=["hbl_no", "mbl_no"]) is None
    assert lookup.last_miss_reason == "ambiguous_source"


def test_independent_instances_merge_under_lock(tmp_path, record):
    path = tmp_path / "cache.json"
    left, right = SemanticCache(path).start_run(), SemanticCache(path).start_run()
    assert left.stage_validated(
        observation(), observation(record.fields.hbl_no.value, seconds=1), fill(), record
    )
    assert right.stage_validated(
        observation(label="Parent identifier"),
        observation(record.fields.mbl_no.value, seconds=1, label="Parent identifier"),
        fill(source="mbl_no"),
        record,
    )
    left.finish(success=True)
    right.finish(success=True)
    assert len(SemanticCache(path).entries()) == 2


def test_current_scope_and_existing_values_are_ineligible(tmp_path, record):
    cache = SemanticCache(tmp_path / "cache.json")
    seed(cache, record)
    session = cache.start_run()
    assert session.lookup(observation(), phase="final_query", allowed_sources=["hbl_no"]) is None
    assert session.lookup(observation(), phase="enter", allowed_sources=[]) is None
    assert (
        session.lookup(
            observation(record.fields.hbl_no.value),
            phase="enter",
            allowed_sources=["hbl_no"],
            record=record,
        )
        is None
    )


def test_complete_safe_fake_reuse_flow(tmp_path, record):
    cache = SemanticCache(tmp_path / "cache.json")
    seed(cache, record)
    session = cache.start_run()
    current = observation(shift=40, seconds=5)
    hit = session.lookup(current, phase="enter", allowed_sources=["hbl_no"], record=record)
    assert hit is not None
    # Fake executor resolves the current choice, then provides a fresh value read.
    assert hit.decision.action.ref in {item.ref for item in current.elements}
    after = observation(record.fields.hbl_no.value, shift=90, seconds=6)
    assert session.stage_validated(current, after, hit.decision, record)
    assert session.finish(success=True) == 1
    assert json.loads(cache.path.read_text())["entries"][0]["success_count"] == 2


def test_concurrent_successes_preserve_all_counts(tmp_path, record):
    path = tmp_path / "cache.json"

    def commit_one(_):
        session = SemanticCache(path).start_run()
        assert session.stage_validated(
            observation(), observation(record.fields.hbl_no.value, seconds=1), fill(), record
        )
        return session.finish(success=True)

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(commit_one, range(8))) == [1] * 8
    assert SemanticCache(path).entries()[0].success_count == 8
