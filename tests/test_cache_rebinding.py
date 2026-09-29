"""Structural drift and model proposals cannot bypass current evidence or promotion."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sawon_interfaces import Observation, UIElement, WindowInfo
from test_semantic_cache import fill
from test_semantic_cache import record as record

from brain.cache import SemanticCache
from brain.cache.rebind import RebindProposal, build_rebind_request, install_rebind_proposal

FIELDS = ("hbl_no", "mbl_no", "shipper", "consignee")
LABELS = ("Reference alpha", "Reference beta", "Party gamma", "Party delta")


def form(*, nested=False, renamed=False, shift=0, seconds=0, values=None, root="Entry surface"):
    window = WindowInfo(handle=7, title=root, process="fixture.exe")
    box = (0.0, 0.0, 100.0, 25.0)
    items = [UIElement(ref=f"e{shift}", role="Window", name=root, bbox=box)]
    parent = items[0].ref
    if nested:
        items.append(
            UIElement(
                ref=f"e{shift + 9}", role="Pane", name="New group", parent_ref=parent, bbox=box
            )
        )
        parent = items[-1].ref
    for index, field in enumerate(FIELDS):
        label = f"Renamed party {index}" if renamed and index >= 2 else LABELS[index]
        items.append(
            UIElement(
                ref=f"e{shift + index + 1}",
                role="Edit",
                name=label,
                parent_ref=parent,
                value=(values or {}).get(field, ""),
                bbox=box,
            )
        )
    return Observation(
        obs_id=uuid4(),
        ts=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds),
        active_window=window,
        windows=[window],
        elements=items,
        screen_hash="1" * 40,
    )


def seeded(tmp_path, record, mode="rebind"):
    cache = SemanticCache(tmp_path / "cache.json", matching_mode=mode)
    session = cache.start_run()
    for index, field in enumerate(FIELDS):
        assert session.stage_validated(
            form(),
            form(seconds=1, values={field: str(getattr(record.fields, field).value)}),
            fill(ref=f"e{index + 1}", source=field),
            record,
        )
    assert session.finish(success=True) == 4
    return cache


def test_structure_only_reuses_unique_labels_and_current_refs(tmp_path, record):
    cache = seeded(tmp_path, record)
    current = form(nested=True, shift=100)
    session = cache.start_run()
    hit = session.lookup(current, phase="enter", allowed_sources=["hbl_no"], record=record)
    assert hit and hit.match_mode == "label_anchor" and hit.decision.action.ref == "e101"
    assert (
        SemanticCache(cache.path).start_run().lookup(current, phase="enter", allowed_sources=FIELDS)
        is None
    )
    original = cache.path.read_bytes()
    assert session.finish(success=True) == 0  # A match alone proves no input.
    assert cache.path.read_bytes() == original


@pytest.mark.parametrize("change", ["root", "duplicate", "one_anchor", "blocked", "wrong_phase"])
def test_relaxation_still_requires_unambiguous_scope(tmp_path, record, change):
    session = seeded(tmp_path, record).start_run()
    current = form(nested=True)
    phase = "enter"
    if change == "root":
        current = form(nested=True, root="Different surface")
    elif change == "duplicate":
        current.elements.append(current.elements[2].model_copy(update={"ref": "e99"}))
    elif change == "one_anchor":
        for item in current.elements[3:]:
            item.name = "Different " + item.name
    elif change == "blocked":
        current.blocked_reason = "outside_allowlist"
    else:
        phase = "final_query"
    assert session.lookup(current, phase=phase, allowed_sources=["hbl_no"], record=record) is None


def proposal_for(request, **changes):
    data = {
        "observation_id": request.observation_id,
        "bindings": [{"field": "shipper", "ref": "e3"}, {"field": "consignee", "ref": "e4"}],
    }
    data.update(changes)
    return RebindProposal.model_validate(data)


def test_model_bindings_remain_local_until_observed_input_and_full_success(tmp_path, record):
    cache = seeded(tmp_path, record)
    before_bytes = cache.path.read_bytes()
    session = cache.start_run()
    current = form(nested=True, renamed=True, seconds=2)
    request = build_rebind_request(session, current, FIELDS)
    assert request and set(request.targets) == {"e3", "e4"}
    prompt = request.prompt()
    assert record.fields.shipper.value not in prompt and '"value"' not in prompt
    assert install_rebind_proposal(session, request, proposal_for(request), current, FIELDS) == 2
    fresh = form(nested=True, renamed=True, shift=100, seconds=3)
    hit = session.lookup(fresh, phase="enter", allowed_sources=["shipper"], record=record)
    assert hit and hit.match_mode == "model_rebind" and hit.decision.action.ref == "e103"
    assert cache.path.read_bytes() == before_bytes
    after = form(
        nested=True,
        renamed=True,
        shift=200,
        seconds=4,
        values={"shipper": record.fields.shipper.value},
    )
    assert session.stage_validated(fresh, after, hit.decision, record)
    assert cache.path.read_bytes() == before_bytes
    assert session.finish(success=True) == 1
    assert len(cache.entries()) == 5  # The other unexecuted model proposal is not persisted.


@pytest.mark.parametrize(
    "bad", ["stale", "unknown", "duplicate_ref", "duplicate_field", "source", "anchor"]
)
def test_invalid_model_response_installs_nothing(tmp_path, record, bad):
    session = seeded(tmp_path, record).start_run()
    current = form(nested=True, renamed=True)
    request = build_rebind_request(session, current, FIELDS)
    data = json.loads(proposal_for(request).model_dump_json())
    if bad == "stale":
        data["observation_id"] = str(uuid4())
    elif bad == "unknown":
        data["bindings"][1]["ref"] = "e999"
    elif bad == "duplicate_ref":
        data["bindings"][1]["ref"] = "e3"
    elif bad == "duplicate_field":
        data["bindings"][1]["field"] = "shipper"
    elif bad == "source":
        data["bindings"][1]["field"] = "description"
    else:
        data["bindings"][1]["ref"] = "e1"
    with pytest.raises(ValueError):
        install_rebind_proposal(
            session, request, RebindProposal.model_validate(data), current, FIELDS
        )
    assert not session.rebound


@pytest.mark.parametrize("kind", ["label", "model"])
def test_failed_rebinding_invalidates_local_proposal_not_original(tmp_path, record, kind):
    cache = seeded(tmp_path, record)
    original = cache.path.read_bytes()
    session = cache.start_run()
    current = form(nested=True, renamed=kind == "model")
    field = "hbl_no" if kind == "label" else "shipper"
    if kind == "model":
        request = build_rebind_request(session, current, FIELDS)
        install_rebind_proposal(session, request, proposal_for(request), current, FIELDS)
    hit = session.lookup(current, phase="enter", allowed_sources=[field], record=record)
    assert hit
    assert not session.record_failure(hit.entry_id)
    assert session.lookup(current, phase="enter", allowed_sources=[field], record=record)
    assert session.record_failure(hit.entry_id)
    assert session.lookup(current, phase="enter", allowed_sources=[field], record=record) is None
    assert cache.path.read_bytes() == original
    assert session.finish(success=False) == 0


def test_model_request_uses_anchors_outside_remaining_fields_and_honors_closed_session(
    tmp_path, record
):
    session = seeded(tmp_path, record).start_run()
    current = form(nested=True, renamed=True)
    request = build_rebind_request(session, current, ("shipper", "consignee"))
    assert request is not None
    session.finish(success=False)
    assert build_rebind_request(session, current, FIELDS) is None
    with pytest.raises(ValueError, match="ineligible"):
        install_rebind_proposal(session, request, proposal_for(request), current, FIELDS)


def test_conflicting_learned_source_is_not_hidden_by_remaining_fields(tmp_path, record):
    cache = seeded(tmp_path, record)
    source = next(e for e in cache.entries() if e.field == "hbl_no")
    cache._promote([source.model_copy(update={"field": "description"})])
    assert (
        cache.start_run().lookup(form(nested=True), phase="enter", allowed_sources=["hbl_no"])
        is None
    )


@pytest.mark.parametrize("change", ["invalid_source", "role", "changed_observation"])
def test_rebind_validates_current_evidence_and_live_source(tmp_path, record, change):
    cache = seeded(tmp_path, record)
    session = cache.start_run()
    current = form(nested=True, renamed=True)
    if change == "role":
        current.elements[-2].role = "ComboBox"
    request = build_rebind_request(session, current, FIELDS)
    if change == "invalid_source":
        entry = next(e for e in cache.entries() if e.field == "shipper")
        cache.record_failure(entry.entry_id)
        cache.record_failure(entry.entry_id)
    elif change == "changed_observation":
        current.elements[-2].name = "Unexpected new control"
    with pytest.raises(ValueError):
        install_rebind_proposal(session, request, proposal_for(request), current, FIELDS)
    assert not session.rebound


def test_model_proposal_with_wrong_input_value_never_promotes(tmp_path, record):
    cache = seeded(tmp_path, record)
    before = cache.path.read_bytes()
    session = cache.start_run()
    current = form(nested=True, renamed=True)
    request = build_rebind_request(session, current, FIELDS)
    install_rebind_proposal(session, request, proposal_for(request), current, FIELDS)
    hit = session.lookup(current, phase="enter", allowed_sources=["shipper"])
    after = form(nested=True, renamed=True, seconds=1, values={"shipper": "incorrect delivery"})
    assert not session.stage_validated(current, after, hit.decision, record)
    assert session.finish(success=True) == 0
    assert cache.path.read_bytes() == before
