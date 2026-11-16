"""Observation facts and explicitly selected input delivery."""

import pytest
from sawon_interfaces import ActionResult
from test_brain import element as raw_element
from test_brain import record as record
from test_screen_decision import setup_brain
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


@pytest.mark.parametrize("intent", ["fill", "query_input"])
def test_synthetic_matching_observation_preserves_requested_input(tmp_path, record, intent):
    """Replay the S1 mismatch: a model reports the value and also requests a write."""
    brain, _, _, _, _, _ = setup_brain(tmp_path, record, [])
    screen_kind = "query" if intent == "query_input" else "editor"
    observed = scene(
        screen_kind,
        [raw_element("e1", "edit", value=brain.values["hbl_no"])],
    )
    brain.observation = observed
    brain.relay.current = observed.wire
    brain.reconciled_observation = reconcile_observation(observed, brain.input_values)
    decision = Decision(
        screen_kind=screen_kind,
        operation="act",
        intent=intent,
        subgoal="Fill current form",
        reason="The input is empty; enter the supplied value.",
        action=Choice(type="set_text", ref="e1", source="hbl_no"),
    )
    sent = []

    def act(action):
        sent.append(action.root)
        return ActionResult(
            action_id=action.root.action_id,
            ok=True,
            duration_ms=0,
            post_obs_id=brain.relay.current.obs_id,
        )

    brain.relay.act = act

    assert brain._execute_plan(ActionPlan.single(decision)) == "observation"
    assert len(sent) == 1
    assert sent[0].type == "set_text"
    assert sent[0].text == brain.values["hbl_no"]
    assert brain.pending is not None
    assert brain.pending["choice"].source == "hbl_no"
