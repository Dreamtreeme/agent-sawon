import pytest
from sawon_interfaces import UIElement
from test_visual_scene import wire

from brain.graph.schemas import Choice, Decision
from brain.graph.screen_decision import MatchAction
from brain.graph.visual_points import VisualPointBindings, VisualPointError
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


@pytest.mark.parametrize("keys", ["pagedown", "pageup"])
def test_wheel_uses_image_point_outside_named_control_but_rejects_outside_capture(keys):
    observation = scene()
    bindings = VisualPointBindings()
    step = MatchAction(target=8, action="key", intent="navigate", keys=keys, point=(300, 150))
    bindings.bind(observation, [step])
    selected = decision(observation).model_copy(
        update={
            "intent": "navigate",
            "action": Choice(type="key", ref=observation.elements[0].ref, keys=keys),
        }
    )
    assert bindings.get(observation, selected).point == (300, 150)
    with pytest.raises(VisualPointError, match="outside_capture") as error:
        bindings.bind(observation, [step.model_copy(update={"point": [400, 150]})])
    assert error.value.detail["image_size"] == [400, 200]
    assert error.value.detail["plan_delivery"] == "not_dispatched"


@pytest.mark.parametrize(
    ("action", "intent", "source", "keys"),
    [
        ("click", "navigate", None, None),
        ("fill", "fill", "hbl_no", None),
        ("key", "navigate", None, "enter"),
    ],
)
def test_wheel_exception_does_not_relax_other_control_points(action, intent, source, keys):
    with pytest.raises(VisualPointError, match="outside_target") as error:
        VisualPointBindings().bind(
            scene(),
            [
                MatchAction(
                    target=8,
                    action=action,
                    intent=intent,
                    source=source,
                    keys=keys,
                    point=(300, 150),
                )
            ],
        )
    assert error.value.detail["loc"] == ["steps", 0, "point"]
    assert error.value.detail["target_bbox"] == [20, 30, 200, 40]
