"""Current screen-model review owns saved-result evidence."""

from types import SimpleNamespace
from uuid import uuid4

from test_brain import element as raw_element
from test_brain import record as record
from test_screen_decision import setup_brain
from test_visual_scene import wire

from brain.visual.scene import SceneObservation


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
