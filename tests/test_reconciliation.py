"""Current pixels, rather than action rituals, drive workflow state."""

from test_brain import element as raw_element
from test_brain import record as record
from test_screen_matching import setup_brain
from test_visual_scene import wire

from brain.graph.execution import ActionPlan
from brain.graph.reconciliation import reconcile_observation
from brain.graph.schemas import Choice, Decision
from brain.observe.values import values_for
from brain.visual.scene import SceneObservation


def scene(kind, elements):
    return SceneObservation(wire=wire(), screen_kind=kind, elements=elements)


def test_unique_current_query_input_is_a_fact_but_result_cells_are_not(record):
    values = values_for(record)
    observed = scene(
        "query",
        [
            raw_element("e1", "edit", value=values["hbl_no"]),
            raw_element("e2", "cell", value=values["hbl_no"]),
        ],
    )

    result = reconcile_observation(observed, values)

    assert result.query_input is not None
    assert result.query_input.ref == "e1"
    ambiguous = scene(
        "query",
        [
            raw_element("e1", "edit", value=values["hbl_no"]),
            raw_element("e2", "combobox", value=values["hbl_no"]),
        ],
    )
    assert reconcile_observation(ambiguous, values).query_input is None

    invoice = scene("query", [raw_element("e9", "edit", value="INV-17")])
    generic = reconcile_observation(
        invoice,
        {"invoice.number": "INV-17"},
        "invoice.number",
    )
    assert generic.query_input is not None
    assert generic.query_input.source == "invoice.number"


def test_observed_query_value_advances_policy_directly_to_search(tmp_path, record):
    brain, _, _, _, _, _ = setup_brain(tmp_path, record, [])
    brain.phase = "final_query"
    brain.duplicate_clear = brain.save_issued = True
    observed = scene(
        "query",
        [raw_element("e1", "edit", value=brain.values["hbl_no"])],
    )
    brain.observation = observed

    brain._apply_observation_reconciliation(observed)

    assert brain.query_value_verified
    assert brain.business_revision == 1
    assert brain._decision_policy().current_step == "search"
    assert any(
        event.payload.get("check") == "observed_state_reconciled" for event in brain.log.read_all()
    )


def test_matching_visible_fill_is_removed_before_dispatch(tmp_path, record):
    brain, _, _, _, _, _ = setup_brain(tmp_path, record, [])
    brain.phase = "enter"
    brain.duplicate_clear = True
    observed = scene(
        "editor",
        [raw_element("e1", "edit", value=brain.values["hbl_no"])],
    )
    brain.observation = observed
    decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="fill",
        subgoal="Fill current form",
        reason="Current model mapping",
        action=Choice(type="set_text", ref="e1", source="hbl_no"),
    )

    reconciled = brain._reconcile_action_plan(ActionPlan.single(decision))

    assert not reconciled.actions
    assert "hbl_no" in brain.verified
    assert brain.business_revision == 1
    assert any(
        event.payload.get("check") == "observed_actions_already_satisfied"
        for event in brain.log.read_all()
    )
