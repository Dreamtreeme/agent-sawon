"""Current screen-model review owns saved-result evidence."""

from types import SimpleNamespace
from uuid import uuid4

from test_brain import element as raw_element
from test_brain import record as record
from test_screen_decision import setup_brain
from test_visual_scene import wire

from brain.graph.save_review import SaveReviewResponse
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


def test_screen_model_review_does_not_bind_clicked_cells_as_saved_row_proof(tmp_path, record):
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

    assert brain.saved_bindings is None
    assert brain.saved_row_binding is None


def test_model_only_task_keeps_post_save_review_with_the_screen_model(tmp_path, record):
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
    assert brain.saved_evidence.session_id is None
    assert brain._current_review().reason == "task_completion_requires_current_model_evidence"
