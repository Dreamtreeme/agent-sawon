import pytest
from sawon_interfaces import UIElement
from test_visual_scene import wire

from brain.graph.matching import MatchAction
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
    if point is None:
        with pytest.raises(ValueError, match=reason):
            MatchAction(target=8, action="click", intent="select_tab")
        return
    bindings = VisualPointBindings()
    with pytest.raises(ValueError, match=reason):
        bindings.bind(
            observation,
            [MatchAction(target=8, action="click", intent="select_tab", point=point)],
        )


def test_nonpointer_model_hint_is_accepted_and_ignored():
    observation = scene()
    bindings = VisualPointBindings()
    step = MatchAction(target=8, action="fill", intent="fill", source="hbl_no", point=(190, 50))
    bindings.bind(observation, [step])
    assert bindings.get(observation, decision(observation)) is None
