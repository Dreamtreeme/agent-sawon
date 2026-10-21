import pytest
from sawon_interfaces import UIElement
from test_visual_scene import wire

from brain.graph.context_recall import MatchAction
from brain.graph.schemas import Choice, Decision
from brain.graph.visual_points import VisualPointBindings
from brain.visual.scene import SceneObservation


def scene():
    observed = wire()
    ref = "e" + str(observed.obs_id.int) + "08"
    return SceneObservation(
        wire=observed,
        screen_kind="editor",
        elements=[UIElement(ref=ref, role="tabitem", name="First Second", bbox=(20, 30, 200, 40))],
    )


def decision(observation, *, ref=None):
    return Decision(
        screen_kind="editor",
        operation="act",
        intent="select_tab",
        subgoal="fill-entry",
        reason="Open the second visible destination",
        action=Choice(type="click", ref=ref or observation.elements[0].ref),
    )


def test_explicit_visual_point_preserves_noncentral_subregion():
    observation = scene()
    bindings = VisualPointBindings()
    bindings.bind(
        observation,
        [MatchAction(target=8, action="click", intent="select_tab", point=(190, 50))],
    )
    assert bindings.get(observation, decision(observation)).point == (190, 50)


@pytest.mark.parametrize(
    ("point", "reason"),
    [(None, "required"), ((230, 50), "outside_target")],
)
def test_visual_point_is_required_and_bound_to_target(point, reason):
    observation = scene()
    bindings = VisualPointBindings()
    with pytest.raises(ValueError, match=reason):
        bindings.bind(
            observation,
            [MatchAction(target=8, action="click", intent="select_tab", point=point)],
        )


@pytest.mark.parametrize(
    ("action", "intent", "source", "keys", "wire_type"),
    [("fill", "fill", "hbl_no", None, "set_text"), ("key", "navigate", None, "tab", "key")],
)
def test_input_and_keyboard_points_survive_compiled_action(action, intent, source, keys, wire_type):
    observation = scene()
    bindings = VisualPointBindings()
    step = MatchAction(
        target=8, action=action, intent=intent, source=source, keys=keys, point=(190.25, 50.75)
    )
    bindings.bind(observation, [step])
    selected = decision(observation).model_copy(
        update={
            "intent": intent,
            "action": Choice(
                type=wire_type, ref=observation.elements[0].ref, source=source, keys=keys
            ),
        }
    )
    assert bindings.get(observation, selected).point == (190.25, 50.75)
    bindings.bind(observation, [step.model_copy(update={"point": None})])
    assert bindings.get(observation, selected) is None  # Same-frame replacement clears old points.


def test_points_are_scoped_to_frame_and_input_source():
    observation = scene()
    bindings = VisualPointBindings()
    bindings.bind(
        observation,
        [
            MatchAction(target=8, action="fill", intent="fill", source=source, point=point)
            for source, point in [("hbl_no", (190, 50)), ("mbl_no", (140, 45))]
        ],
    )
    for source, point in [("hbl_no", (190, 50)), ("mbl_no", (140, 45))]:
        selected = decision(observation).model_copy(
            update={
                "intent": "fill",
                "action": Choice(type="set_text", ref=observation.elements[0].ref, source=source),
            }
        )
        assert bindings.get(observation, selected).point == point, source
        assert bindings.get(scene(), selected) is None, source
