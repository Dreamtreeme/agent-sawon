"""Synthetic execution uses the observed model plan without a second click judge."""

from uuid import uuid4

import pytest
from sawon_interfaces import ActionResult
from test_brain import record as record
from test_screen_decision import named_response, setup_brain

from brain.visual.scanning import VisualGroundingError


@pytest.mark.parametrize(
    "next_action,next_point",
    [("click", (110, 35)), ("click", (160, 35)), ("double_click", (160, 35))],
)
def test_synthetic_unchanged_screen_executes_current_plan_without_extra_call(
    tmp_path, record, next_action, next_point
):
    # The existing fake supplies identical captures and a new decision for the same control.
    def plan(action, point):
        return named_response(
            screen_kind="editor",
            fields={"Panels": {"role": "tabitem", "bbox": [20, 20, 180, 30]}},
            procedure_step="task-action",
            steps=[{"target": "Panels", "action": action, "intent": "select_tab", "point": point}],
        )

    brain, model, relay, *_ = setup_brain(
        tmp_path, record, [plan("click", [110, 35]), plan(next_action, next_point)]
    )
    delivered = []

    def act(action):
        delivered.append(action.root)
        return ActionResult(
            action_id=action.root.action_id, ok=True, post_obs_id=uuid4(), duration_ms=1
        )

    relay.act = act
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert delivered[0].target.image_point == (110, 35)

    assert brain._verify_node() == "review"
    assert brain.result is None and brain.failures == {}
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert delivered[1].type == next_action
    assert delivered[1].target.image_point == next_point
    assert len(model.prompts) == 2, "only the two screen observations may call the model"
    transitions = [
        e.payload["transition"]
        for e in brain.log.read_all()
        if e.payload.get("check") == "action_transition"
    ]
    assert any(t["observed_effect"] == "unknown" for t in transitions)


def test_execution_without_review_authorization_does_not_dispatch(tmp_path, record):
    brain, _, relay, *_ = setup_brain(tmp_path, record, [named_response()])
    brain._observe()
    assert brain._select_node() == "review"
    delivered = []
    relay.act = delivered.append

    assert brain._act_node() == "finish"
    assert brain.result.reason == "execution_review_required"
    assert delivered == []


def test_grounding_failure_does_not_start_another_outer_repair(tmp_path, record, monkeypatch):
    brain, model, relay, *_ = setup_brain(tmp_path, record, [named_response()])
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    relay.act = lambda action: ActionResult(
        action_id=action.root.action_id, ok=True, post_obs_id=uuid4(), duration_ms=1
    )
    assert brain._act_node() == "observation"

    def exhausted():
        raise VisualGroundingError("ocr_marker_text_mismatch")

    monkeypatch.setattr(brain, "_observe", exhausted)
    assert brain._verify_node() == "finish"
    assert brain.result.reason == "visual_grounding:ocr_marker_text_mismatch"
    assert len(model.prompts) == 1
