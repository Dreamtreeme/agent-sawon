"""Fresh saved-result proof, linked detail tabs, and real graph completion."""

from uuid import uuid4

import pytest
from test_brain import element as raw_element
from test_brain import record as record
from test_screen_matching import setup_brain
from test_visual_scene import wire

from brain.graph.saved_evidence import SavedEvidence
from brain.graph.schemas import Choice, Decision
from brain.observe.values import field_matches, values_for
from brain.visual.scene import SceneObservation


def ref(name):
    return "e" + str(int.from_bytes(name.encode(), "big"))


def element(identifier, role, *, parent=None, **kwargs):
    return raw_element(ref(identifier), role, parent=ref(parent) if parent else None, **kwargs)


def scene(kind, elements, *, handle=1):
    w = wire()
    w.screen_hash = uuid4().hex + "a" * 8
    w.active_window = w.active_window.model_copy(update={"handle": handle})
    w.windows = [w.active_window]
    return SceneObservation(wire=w, screen_kind=kind, elements=elements)


def query(values, *, omit=(), identity=None):
    items = [element("grid", "table"), element("row", "row", parent="grid")]
    items += [
        element(
            name, "cell", value=identity if name == "hbl_no" and identity else value, parent="row"
        )
        for name, value in values.items()
        if name not in omit
    ]
    items += [element("open", "button", name="Details")]
    return scene("query", items)


def detail(values, *, handle=2):
    return scene(
        "editor",
        [element(k, "edit", name=k, value=v) for k, v in values.items()]
        + [element("tab", "tabitem", name="More")],
        handle=handle,
    )


def observe(proof, s, values):
    mapping = {k: ref(k) for k in values if any(e.ref == ref(k) for e in s.elements)}
    return proof.observe(s, values, field_matches, mapping)


def action(s, intent="navigate", target="open", type="click"):
    return Decision(
        screen_kind=s.screen_kind,
        operation="act",
        intent=intent,
        subgoal="inspect",
        reason="Inspect saved record",
        action=Choice(type=type, ref=ref(target), source=target if type == "set_text" else None),
    )


def linked(values):
    proof = SavedEvidence()
    proof.begin()
    q = query(values, omit=("description", "container_no"))
    assert observe(proof, q, values).status == "needs_more"
    proof.after_action(action(q), q)
    d = detail({"hbl_no": values["hbl_no"], "description": values["description"]})
    assert observe(proof, d, values).status == "needs_more"
    return proof, d


def test_post_save_query_and_two_detail_tabs_complete(record):
    values = values_for(record)
    proof, d = linked(values)
    proof.after_action(action(d, "select_tab", "tab"), d)
    last = detail({"container_no": values["container_no"]})
    assert observe(proof, last, values).status == "ready"
    assert proof.query_identity["screen_kind"] == "query"
    assert proof.fields["description"]["screen_kind"] == "editor"
    assert proof.fields["container_no"]["identity_obs_id"] == str(d.obs_id)
    assert len({v["obs_id"] for v in proof.fields.values()}) == 3


@pytest.mark.parametrize(
    "mode",
    [
        "other_record",
        "other_window",
        "no_tab_transition",
        "conflict",
        "clipped",
        "ascii_clipped",
        "empty",
    ],
)
def test_unlinked_or_incomplete_detail_never_completes(record, mode):
    values = values_for(record)
    proof, d = linked(values)
    if mode != "no_tab_transition":
        proof.after_action(action(d, "select_tab", "tab"), d)
    content = {"container_no": values["container_no"]}
    if mode == "other_record":
        content["hbl_no"] = "OTHER"
    if mode == "conflict":
        content["description"] = "Wrong cargo"
    if mode == "clipped":
        content["container_no"] = "TEST…"
    if mode == "ascii_clipped":
        content["container_no"] = "TEST.."
    if mode == "empty":
        content["container_no"] = ""
    last = detail(content, handle=3 if mode == "other_window" else 2)
    result = observe(proof, last, values)
    assert result.status != "ready"
    if mode in {"other_record", "conflict"}:
        assert result.status == "blocked"


def test_new_query_and_edit_invalidate_all_old_proof(record):
    values = values_for(record)
    proof, d = linked(values)
    proof.after_action(action(d, "fill", "description", "set_text"), d)
    assert proof.session_id is None and not proof.fields
    proof.begin()
    assert not proof.query_row_seen
    assert observe(proof, detail(values), values).status != "ready"
    proof.begin()
    observe(proof, query(values), values)
    changed = query(values, identity="OTHER")
    assert observe(proof, changed, values).status != "ready"
    assert not proof.fields and not proof.query_row_seen


def test_pre_search_frame_and_editor_input_history_are_not_proof(record):
    values = values_for(record)
    q = query(values)
    proof = SavedEvidence()
    proof.begin(str(q.obs_id))
    assert observe(proof, q, values).reason == "fresh_query_result_required"
    assert observe(proof, detail(values), values).status != "ready"
    assert not proof.fields


def test_duplicate_rows_block_and_same_cell_cannot_prove_several_fields(record):
    values = values_for(record)
    proof = SavedEvidence()
    proof.begin()
    q = query(values)
    q = q.model_copy(
        update={
            "elements": [
                *q.elements,
                element("other-row", "row", parent="grid"),
                element("other-id", "cell", parent="other-row", value=values["hbl_no"]),
            ]
        }
    )
    assert observe(proof, q, values).status == "blocked"
    proof, d = linked(values)
    proof.after_action(action(d, "select_tab", "tab"), d)
    last = detail({"container_no": values["container_no"]})
    mapping = {"container_no": ref("container_no"), "description": ref("container_no")}
    assert proof.observe(last, values, field_matches, mapping).status != "ready"


def test_merged_query_cell_is_missing_evidence_not_a_field_conflict(record):
    values = values_for(record)
    proof = SavedEvidence()
    proof.begin()
    q = query(values, omit=("mbl_no", "shipper"))
    merged = element(
        "merged",
        "cell",
        parent="row",
        value=values["mbl_no"] + values["shipper"],
    )
    q = q.model_copy(update={"elements": [*q.elements, merged]})

    result = proof.observe(
        q,
        values,
        field_matches,
        {"mbl_no": merged.ref, "shipper": merged.ref},
    )

    assert result.status == "needs_more"
    assert result.reason == "saved_record_evidence_missing"
    assert {"mbl_no", "shipper"} <= set(result.missing_fields)
    assert proof.conflict is None


def test_graph_review_completes_from_unambiguous_current_inputs_without_rematch(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "final_query"
    brain.save_issued = brain.duplicate_clear = True
    brain.query_submitted = brain.query_value_verified = True
    brain.saved_evidence.begin()
    values = brain.values
    q = query(values, omit=("description", "container_no"))
    brain.observation = q
    brain.review = None
    assert brain._review_node({}) == {"next": "select"}
    brain.saved_evidence.after_action(action(q), q)
    d = detail({"hbl_no": values["hbl_no"], "description": values["description"]})
    brain.observation = d
    brain.review = None
    brain.saved_bindings = (
        str(d.obs_id),
        {"hbl_no": ref("hbl_no"), "description": ref("description")},
    )
    assert brain._review_node({}) == {"next": "select"}
    brain.saved_evidence.after_action(action(d, "select_tab", "tab"), d)
    last = detail({"container_no": values["container_no"]})
    brain.observation = last
    brain.review = None
    assert brain._review_node({}) == {"next": "finish"}
    assert brain.result.status == "success"
    assert brain.query_descriptor is None
    assert (tmp_path / "saved-evidence.json").exists()
    assert not brain.model.prompts
