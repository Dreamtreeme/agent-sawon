"""Synthetic captures exercise ORB and the actual post-action verification path."""

import json
from uuid import uuid4

import cv2
import numpy as np
import pytest
from test_brain import element
from test_brain import record as record
from test_screen_decision import named_response, setup_brain
from test_visual_scene import wire

from brain.graph.schemas import Choice, Decision
from brain.graph.transitions import ActionTransition, ExpectedEffect, ObservedEffect
from brain.observe.progress import compare_navigation, navigation_state, navigation_target
from brain.visual.frame_change import compare_frames
from brain.visual.scene import SceneObservation


def synthetic_form():
    """Draw a fabricated form; no private ERP screenshots are committed."""
    image = np.full((700, 900), 245, np.uint8)
    rng = np.random.default_rng(110)
    for row in range(10):
        y = 45 + row * 60
        cv2.putText(image, f"Field {rng.integers(1000, 9999)}", (30, y), 0, 0.7, 20, 1)
        cv2.rectangle(image, (250, y - 25), (800, y + 5), 170, 1)
        cv2.putText(image, str(rng.integers(10000, 99999)), (265, y), 0, 0.7, 20, 1)
    return image


def save_capture(case_dir, scene, image):
    path = case_dir / "visual" / str(scene.obs_id) / "capture.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(cv2.imencode(".png", image)[1].tobytes())
    return path


@pytest.mark.parametrize("change", ["focus", "scroll", "missing"])
def test_synthetic_orb_effect_drives_verify_without_phash_equality(
    tmp_path, record, monkeypatch, change
):
    brain, model, *_ = setup_brain(tmp_path, record, [])
    before = SceneObservation(
        wire=wire(), elements=[element("e1", "Pane", name="Viewport")], capture_phash=10
    )
    # Focus has a different hash, scrolling has the same hash: neither is the oracle.
    after = SceneObservation(
        wire=wire(), elements=before.elements, capture_phash=12 if change == "focus" else 10
    )
    image = synthetic_form()
    save_capture(tmp_path, before, image)
    if change == "focus":
        image[48:50, 250:800] = 70
    elif change == "scroll":
        image = cv2.warpAffine(image, np.float32([[1, 0, 0], [0, 1, -25]]), (900, 700))
    if change != "missing":
        save_capture(tmp_path, after, image)
    decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="navigate",
        subgoal="Reveal remaining fields",
        reason="Move the viewport",
        action=Choice(type="key", keys="pagedown", ref="e1"),
    )
    brain.phase, brain.observation, brain.decision = "task", before, decision
    brain.pending = {
        "before_observation": before,
        "choice": decision.action,
        "decision": decision,
        "source_window_handle": 1,
        "source_navigation_state": navigation_state(before),
        "transition": ActionTransition(
            action_id=str(uuid4()),
            before_obs_id=str(before.obs_id),
            intent="navigate",
            expected_effect=ExpectedEffect.NAVIGATION_CHANGE,
        ),
    }

    def observe():
        brain.observation = after
        return after

    verified = []
    monkeypatch.setattr(brain, "_observe", observe)
    monkeypatch.setattr(
        type(brain.memory_retrieval),
        "verify_delivery",
        lambda self, *, success: verified.append(success),
    )
    route = brain._verify_node()
    expected = {"focus": False, "scroll": True, "missing": None}[change]
    assert verified == [expected], change
    assert route == ("observation" if change == "focus" else "review"), change
    events = [event.payload for event in brain.log.read_all()]
    comparison = next(e for e in events if e.get("check") == "screen_structure_comparison")
    assert comparison["changed"] is expected
    assert comparison["before_obs_id"] == str(before.obs_id)
    assert comparison["after_obs_id"] == str(after.obs_id)
    transition = next(e["transition"] for e in events if e.get("check") == "action_transition")
    assert (
        transition["observed_effect"]
        == {
            "focus": "no_change",
            "scroll": "changed",
            "missing": "unknown",
        }[change]
    )
    assert transition["after_obs_id"] == str(after.obs_id)
    assert not model.prompts, "ORB must not add a model call"


def test_synthetic_featureless_capture_is_unknown_but_window_and_size_changes_are_known(tmp_path):
    before = SceneObservation(wire=wire(), elements=[], capture_phash=0)
    after = SceneObservation(wire=wire(), elements=[], capture_phash=0)
    a = save_capture(tmp_path, before, np.full((100, 200), 255, np.uint8))
    b = save_capture(tmp_path, after, np.full((100, 200), 255, np.uint8))
    result = compare_frames(a, b)
    assert result["changed"] is None and result["matches"] == 0
    # Unknown structure must still retain independently observed action facts.
    transition = ActionTransition(
        action_id="synthetic-save",
        before_obs_id="before",
        intent="save",
        expected_effect=ExpectedEffect.SAVE_ACKNOWLEDGEMENT,
    ).complete("after", ObservedEffect.UNKNOWN, ("save_issued",))
    assert transition.after_obs_id == "after" and transition.new_facts == ("save_issued",)
    b.write_bytes(cv2.imencode(".png", np.full((120, 200), 255, np.uint8))[1].tobytes())
    assert compare_frames(a, b)["reason"] == "capture_size_changed"
    assert compare_frames(a, b)["changed"] is True
    after.wire.active_window.handle = 2
    assert compare_navigation(before, after, tmp_path) == {
        "method": "window",
        "changed": True,
        "reason": "window_changed",
    }


@pytest.mark.parametrize("next_action", ["scroll", "same"])
def test_synthetic_capture_feedback_precedes_model_and_preserves_alternative(
    tmp_path, record, monkeypatch, next_action
):
    """Synthetic 110 failure: real scanner/model boundary, no desktop or paid calls."""
    from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES

    response = named_response(
        screen_kind="editor",
        steps=[
            {
                "target": "Reference",
                "action": "key",
                "intent": "navigate",
                "keys": "pagedown",
                "point": [175, 30],
            }
        ],
    )
    if next_action == "same":
        response = named_response(
            screen_kind="editor",
            fields={"Label": {"role": "text", "bbox": [100, 15, 150, 30]}},
            assignments={},
            steps=[
                {"target": "Label", "action": "click", "intent": "navigate", "point": [175, 30]}
            ],
        )
    brain, model, relay, ocr, *_ = setup_brain(tmp_path, record, [response])
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})
    before = SceneObservation(
        wire=wire(), elements=[element("e1", "Text", name="Label")], capture_phash=10
    )
    image = cv2.resize(synthetic_form(), (400, 200))
    save_capture(tmp_path, before, image)
    decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="navigate",
        subgoal="Expose field",
        reason="Focus",
        action=Choice(type="click", ref="e1"),
    )
    brain.phase, brain.observation, brain.decision = "task", before, decision
    brain.pending = {
        "before_observation": before,
        "choice": decision.action,
        "decision": decision,
        "source_window_handle": 1,
        "source_navigation_state": navigation_state(before),
        "source_screen_path": ["Synthetic", "Main"],
        "click_target": navigation_target(before.elements[0], before),
        "exploration_action_key": "click:label",
        "exploration_goal_key": brain._exploration_goal_key(),
        "exploration_action": {"type": "click", "target": {"name": "Label"}},
        "transition": ActionTransition(
            action_id="synthetic-focus",
            before_obs_id=str(before.obs_id),
            intent="navigate",
            expected_effect=ExpectedEffect.NAVIGATION_CHANGE,
        ),
    }
    # Old code would terminate before allowing the newly proposed wheel action.
    brain.failures["task:explore:click:label"] = brain.settings.max_subgoal_failures

    def screenshot(observation, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cv2.imencode(".png", image)[1].tobytes())
        return path

    read = ocr.read

    def read_after_comparison(page):
        assert brain.pending["structure_observation"]["changed"] is False
        # A repeated interpretation/callback must not duplicate evidence or counters.
        brain.screen_scanner.on_capture(relay.current)
        return read(page)

    monkeypatch.setattr(relay, "screenshot", screenshot)
    monkeypatch.setattr(ocr, "read", read_after_comparison)
    assert brain._verify_node() == ("review" if next_action == "scroll" else "finish")
    if next_action == "scroll":
        assert brain.result is None and brain.visual_proposal.steps[0].keys == "pagedown"
    else:
        assert brain.result.reason.startswith("repeated_subgoal_failure:")
    assert len(model.prompts) == 1
    request = json.loads(model.prompts[0][len(CONTEXT_MAPPING_RULES) :])
    assert request["execution"]["last_action_observation"]["changed"] is False
    history = request["notebook"]["failed_transitions"]
    assert len(history) == 1 and history[0]["outcome"] == "no_change"
    assert history[0]["action"]["screen_path"] == ["Synthetic", "Main"]
    assert history[0]["scope"] == "historical_observation"
    assert navigation_state(before) != navigation_state(brain.observation)
    assert brain._failed_exploration_transitions() == history
    events = brain.log.read_all()
    comparisons = [e for e in events if e.payload.get("check") == "screen_structure_comparison"]
    calls = [e for e in events if e.kind == "model_call"]
    assert len(comparisons) == 1 and comparisons[0].seq < calls[0].seq
    assert brain.failures["task:explore:click:label"] == brain.settings.max_subgoal_failures + (
        next_action == "same"
    )
