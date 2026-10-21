"""Fresh saved-result proof, linked detail tabs, and real graph completion."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_brain import element as raw_element
from test_brain import record as record
from test_context_recall import setup_brain
from test_visual_scene import wire

from brain.graph.save_review import SaveReviewResponse
from brain.graph.saved_evidence import SavedEvidence
from brain.graph.schemas import Choice, Decision
from brain.observe.values import field_matches, values_for
from brain.task_instruction import TaskInstruction
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


@pytest.mark.parametrize("bind_query_input", [False, True])
def test_unique_selected_row_links_clipped_identifier_to_exact_query_input(
    record, bind_query_input
):
    values = values_for(record)
    proof = SavedEvidence()
    proof.begin()
    q = query(values, identity=values["hbl_no"][:8] + "...")
    query_input = element("query-input", "edit", name="House reference", value=values["hbl_no"])
    q = q.model_copy(update={"elements": [*q.elements, query_input]})

    result = proof.observe(
        q,
        values,
        field_matches,
        {"hbl_no": query_input.ref} if bind_query_input else {},
        selected_row_ref=ref("row"),
    )

    assert result.status == "ready"
    assert proof.query_row_seen is True
    assert proof.query_identity["value"] == values["hbl_no"]
    assert proof.query_row_ref == ref("row")
    assert result.missing_fields == ()

    # Selecting the exact row linked by the current query must preserve the
    # saved-record session even when the row renders only a clipped identifier.
    proof.after_action(action(q), q)
    assert proof.session_id is not None
    assert proof.query_row_seen is True


def test_selected_row_uses_exact_query_input_when_model_binds_row_text(record):
    values = values_for(record)
    proof = SavedEvidence()
    proof.begin()
    q = query(values, identity=values["hbl_no"][:8] + ".")
    query_input = element("query-input", "edit", name="House reference", value=values["hbl_no"])
    misleading = element(
        "row-hbl-text",
        "text",
        parent="row",
        value=values["hbl_no"][:8] + ".",
    )
    q = q.model_copy(update={"elements": [*q.elements, query_input, misleading]})

    result = proof.observe(
        q,
        values,
        field_matches,
        {"hbl_no": misleading.ref},
        selected_row_ref=ref("row"),
    )

    assert result.status == "ready"
    assert proof.query_row_seen is True
    assert proof.query_identity["ref"] == query_input.ref


def test_unique_selected_row_links_to_exact_submitted_query_when_input_is_not_detected(record):
    values = values_for(record)
    proof = SavedEvidence()
    pre_search = scene(
        "query",
        [element("query-input", "edit", value=values["hbl_no"])],
    )
    proof.begin(str(pre_search.obs_id))
    assert proof.remember_submitted_query(pre_search, values["hbl_no"])
    result = query(values, identity=values["hbl_no"][:8] + "...")

    review = proof.observe(
        result,
        values,
        field_matches,
        {"hbl_no": ref("row")},
        selected_row_ref=ref("row"),
    )

    assert review.matching_rows == 1
    assert proof.query_row_seen is True
    assert proof.query_identity["proof_kind"] == "submitted_query_input"


def test_screen_binding_promotes_a_clicked_result_cell_to_its_parent_row(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    observation = scene("query", [])
    prefix = "e" + str(observation.obs_id.int) + "0"
    brain.observation = observation.model_copy(
        update={
            "elements": [
                raw_element(prefix + "1", "row"),
                raw_element(prefix + "2", "cell", parent=prefix + "1"),
            ]
        }
    )
    response = SimpleNamespace(
        evidence=[],
        controls=[
            SimpleNamespace(id=1, role="row", parent_id=None),
            SimpleNamespace(id=2, role="cell", parent_id=1),
        ],
        steps=[SimpleNamespace(target=2, action="click")],
    )

    brain._bind_saved_evidence(response)

    assert brain.saved_row_binding == (str(observation.obs_id), prefix + "1")


def test_different_result_row_still_resets_selected_row_link(record):
    values = values_for(record)
    proof = SavedEvidence()
    proof.begin()
    q = query(values, identity=values["hbl_no"][:8] + "...")
    query_input = element("query-input", "edit", name="House reference", value=values["hbl_no"])
    other_row = element("other-row", "row", parent="grid")
    q = q.model_copy(update={"elements": [*q.elements, query_input, other_row]})

    # Establish the link on an unambiguous copy first, then prove that choosing
    # another currently visible row cannot inherit it.
    linked_q = q.model_copy(update={"elements": [e for e in q.elements if e.ref != other_row.ref]})
    proof.observe(
        linked_q,
        values,
        field_matches,
        {},
        selected_row_ref=ref("row"),
    )
    proof.after_action(action(q, target="other-row"), q)

    assert proof.session_id is None
    assert proof.query_row_seen is False


def test_identical_capture_renewal_preserves_the_same_unique_selected_row(record):
    values = values_for(record)
    proof = SavedEvidence()
    proof.begin()
    q = query(values, identity=values["hbl_no"][:8] + "...")
    query_input = element("query-input", "edit", value=values["hbl_no"])
    q = q.model_copy(update={"elements": [*q.elements, query_input]})
    proof.observe(q, values, field_matches, {}, selected_row_ref=ref("row"))

    renewed_row = element("renewed-row", "row", parent="renewed-grid")
    renewed = q.model_copy(
        update={
            "wire": q.wire.model_copy(update={"obs_id": uuid4()}),
            "elements": [
                element("renewed-grid", "table"),
                renewed_row,
                element("renewed-query-input", "edit", value=values["hbl_no"]),
            ],
        }
    )
    proof.after_action(action(renewed, target="renewed-row"), renewed)

    assert proof.session_id is not None
    assert proof.query_row_seen is True


def test_selected_unique_row_survives_focus_repaint_before_open_button(record):
    values = values_for(record)
    proof = SavedEvidence()
    proof.begin()
    first = query(
        values,
        omit=("description", "container_no"),
        identity=values["hbl_no"][:8] + "...",
    )
    first_input = element("first-query-input", "edit", value=values["hbl_no"])
    first = first.model_copy(update={"elements": [*first.elements, first_input]})
    initial = proof.observe(
        first,
        values,
        field_matches,
        {"hbl_no": first_input.ref},
        selected_row_ref=ref("row"),
    )
    assert initial.matching_rows == 1
    assert {"consignee", "shipper"} <= set(proof.fields)

    # The selected-row highlight changes the capture and all observation refs.
    # The next plan clicks a separate open button, so it has no selected_row_ref.
    repainted = query(
        values,
        omit=("description", "container_no"),
        identity=values["hbl_no"][:8] + "...",
    )
    repainted_input = element("repainted-query-input", "text", value=values["hbl_no"])
    repainted = repainted.model_copy(update={"elements": [*repainted.elements, repainted_input]})
    carried = proof.observe(
        repainted,
        values,
        field_matches,
        {"hbl_no": repainted_input.ref},
    )

    assert carried.matching_rows == 1
    assert proof.query_row_seen is True
    assert proof.query_row_ref == ref("row")

    proof.after_action(action(repainted, target="open"), repainted)
    opened = detail(
        {
            "hbl_no": values["hbl_no"],
            "description": values["description"],
        }
    )
    opened_review = observe(proof, opened, values)
    assert opened_review.status == "needs_more"
    assert proof.query_row_seen is True
    assert proof.detail_identity is not None
    assert "description" in proof.fields


def test_selected_row_repaint_does_not_carry_changed_row(record):
    values = values_for(record)
    proof = SavedEvidence()
    proof.begin()
    first = query(values, identity=values["hbl_no"][:8] + "...")
    first_input = element("first-query-input", "edit", value=values["hbl_no"])
    first = first.model_copy(update={"elements": [*first.elements, first_input]})
    proof.observe(
        first,
        values,
        field_matches,
        {"hbl_no": first_input.ref},
        selected_row_ref=ref("row"),
    )

    changed = query(values, identity=values["hbl_no"][:8] + "...")
    changed_input = element("changed-query-input", "text", value=values["hbl_no"])
    changed_elements = [
        item.model_copy(update={"value": "OTHER CONSIGNEE", "name": "OTHER CONSIGNEE"})
        if item.ref == ref("consignee")
        else item
        for item in changed.elements
    ]
    changed = changed.model_copy(update={"elements": [*changed_elements, changed_input]})
    result = proof.observe(
        changed,
        values,
        field_matches,
        {"hbl_no": changed_input.ref},
    )

    assert result.matching_rows == 0
    assert proof.query_row_seen is False
    assert not proof.fields


def test_detail_uses_unique_exact_identity_when_model_binding_is_not_an_input(record):
    values = values_for(record)
    proof = SavedEvidence()
    proof.begin()
    q = query(values)
    assert observe(proof, q, values).status == "ready"
    proof.after_action(action(q, target="row"), q)
    d = detail({"hbl_no": values["hbl_no"], "description": values["description"]})
    label = element("hbl-label", "text", name="House B/L")
    d = d.model_copy(update={"elements": [*d.elements, label]})

    result = proof.observe(
        d,
        values,
        field_matches,
        {"hbl_no": label.ref, "description": ref("description")},
    )

    assert result.status == "ready"
    assert proof.detail_identity is not None
    assert "description" in proof.fields


def test_linked_read_only_detail_accepts_exact_current_value_text(record):
    values = values_for(record)
    proof = SavedEvidence()
    proof.begin()
    q = query(values, omit=("description", "container_no"))
    assert observe(proof, q, values).status == "needs_more"
    proof.after_action(action(q, target="row"), q)
    hbl = element("read-hbl", "text", value=values["hbl_no"])
    description = element("read-description", "text", value=values["description"])
    unrelated = element("unbound-value", "text", value=values["container_no"])
    d = scene("editor", [hbl, description, unrelated], handle=2)

    result = proof.observe(
        d,
        values,
        field_matches,
        {"hbl_no": hbl.ref, "description": description.ref},
    )

    assert proof.detail_identity is not None
    assert "description" in proof.fields
    assert "container_no" in proof.fields
    assert result.status == "ready"


def test_generic_task_review_joins_post_save_query_and_detail_tabs(tmp_path, record):
    task_instruction = TaskInstruction(
        name="Register shipment",
        objective="Save and requery the supplied shipment.",
        identity_input_key="hbl_no",
        completion_conditions=["The saved shipment values match after requery."],
    )
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    brain.save_issued = True
    brain.saved_evidence.begin()
    values = brain.values
    q = query(values, omit=("description", "container_no"))
    brain.observation = q
    brain.review = None
    assert brain._review_node() == "decision"
    brain.saved_evidence.after_action(action(q), q)
    d = detail({"hbl_no": values["hbl_no"], "description": values["description"]})
    brain.observation = d
    brain.review = None
    brain.saved_bindings = (
        str(d.obs_id),
        {"hbl_no": ref("hbl_no"), "description": ref("description")},
    )
    assert brain._review_node() == "decision"
    brain.saved_evidence.after_action(action(d, "select_tab", "tab"), d)
    last = detail({"container_no": values["container_no"]})
    brain.observation = last
    brain.review = None

    final_review = brain._current_review()
    assert final_review.missing_fields == ()
    assert final_review.status == "ready"
    assert brain._review_node() == "finish"
    assert brain.result.status == "success"
    assert brain.result.reason == "saved_and_verified_awaiting_approval"
    assert brain.query_descriptor is None
    assert (tmp_path / "saved-evidence.json").exists()
    assert not brain.model.prompts


def test_generic_visual_task_starts_saved_evidence_after_save_review(tmp_path, record):
    task_instruction = TaskInstruction(
        name="Register shipment",
        objective="Save and requery the supplied shipment.",
        identity_input_key="hbl_no",
        completion_conditions=["The saved shipment values match after requery."],
    )
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    brain.phase = "save_result"
    message = element("saved", "text", name="Saved")
    brain.observation = scene("dialog", [message])
    review = SaveReviewResponse.model_validate(
        {
            "result": "ready_for_query",
            "evidence_refs": [message.ref],
            "repair_fields": [],
            "next_decision": {
                "decision": {
                    "screen_kind": "dialog",
                    "operation": "stop",
                    "intent": "stop",
                    "subgoal": "Review saved record",
                    "reason": "Plan from the fresh query screen",
                    "action": None,
                }
            },
        }
    )

    assert brain._apply_save_review(review) is None
    assert brain.phase == "task"
    assert brain.saved_evidence.session_id is not None
    assert brain.saved_evidence.origin_obs_id == str(brain.observation.obs_id)


def test_model_only_task_keeps_post_save_review_with_the_screen_model(tmp_path, record):
    task_instruction = TaskInstruction(
        name="Register shipment",
        objective="Save and requery the supplied shipment.",
        identity_input_key="hbl_no",
        completion_conditions=["The saved shipment values match after requery."],
    )
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    brain.phase = "save_result"
    message = element("saved", "text", name="Saved")
    brain.observation = scene("dialog", [message])
    review = SaveReviewResponse.model_validate(
        {
            "result": "ready_for_query",
            "evidence_refs": [message.ref],
            "repair_fields": [],
            "next_decision": {
                "decision": {
                    "screen_kind": "dialog",
                    "operation": "stop",
                    "intent": "stop",
                    "subgoal": "Review saved record",
                    "reason": "Plan from the fresh query screen",
                    "action": None,
                }
            },
        }
    )

    assert brain._apply_save_review(review) is None
    assert brain.phase == "task"
    assert brain.saved_evidence.session_id is None
    assert brain._current_review().reason == "task_completion_requires_current_model_evidence"
