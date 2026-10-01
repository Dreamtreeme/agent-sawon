"""Real executor regressions: OCR center miss -> one measured image correction."""

from uuid import uuid4

import pytest
from PIL import Image
from sawon_interfaces import ActionResult
from test_brain import ScriptedModel, element
from test_brain import record as record
from test_visual_scene import wire

from brain.graph.engine import Brain
from brain.graph.schemas import Choice, Decision
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings
from brain.visual.scene import SceneObservation


def setup(tmp_path, record, answer, *, success=True, enabled=True):
    class Relay:
        changed = False
        actions = []
        stale = False

        def observe(self):
            self.current = wire()
            if self.stale:
                self.current.screen_hash = "b" * 40
            return self.current

        def screenshot(self, obs, path):
            Image.new("RGB", (400, 200), "white").save(path)

        def act(self, action):
            assert action.root.obs_id == self.current.obs_id
            assert action.root.target.capture_id == self.current.screenshot.geometry.capture_id
            self.actions.append(action)
            if action.root.target.image_point[0] > 130 and success:
                self.changed = True
            return ActionResult(
                action_id=action.root.action_id, ok=True, post_obs_id=uuid4(), duration_ms=1
            )

    relay = Relay()
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="test")
    model = ScriptedModel(log, [answer])
    generate = model._generate

    def capture(prompt, schema, *, images=None):
        assert images and images[0].name == "ocr-markers.png"
        assert "Open the second panel" in prompt
        assert "navigation_no_progress" in prompt
        return generate(prompt, schema, images=images)

    model._generate = capture
    brain = Brain(
        record=record,
        goal="Open the second panel",
        relay=relay,
        model=model,
        settings=BrainSettings(perception_mode="visual_only", visual_click_refinement=enabled),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    brain.phase = "enter"
    brain.duplicate_clear = True
    brain.steps = 1

    def observe():
        current = relay.observe()
        tab = element("e" + str(current.obs_id.int), "tabitem", name="First Second")
        tab.bbox = (20, 20, 180, 30)
        body = element(
            "e" + str(current.obs_id.int) + "0",
            "text",
            name="Second panel" if relay.changed else "First panel",
        )
        body.bbox = (20, 90, 180, 30)
        brain.observation = SceneObservation(
            wire=current, screen_kind="editor", elements=[tab, body]
        )
        return brain.observation

    brain._observe = observe
    observe()
    brain.decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="select_tab",
        subgoal="switch-panel",
        reason="Open the second panel",
        action=Choice(type="click", ref=brain.observation.elements[0].ref),
    )
    return brain, relay, model


def test_failed_center_click_refines_once_then_uses_shared_execution_and_verification(
    tmp_path, record
):
    brain, relay, model = setup(
        tmp_path,
        record,
        {"outcome": "retry", "x": 160, "y": 35, "explanation": "Second caption is on the right"},
    )
    assert brain._act_node({}) == {"next": "verify"}
    assert not model.prompts  # No image request before a failed attempt.
    assert relay.actions[0].root.target.image_point == (110, 35)
    assert brain._verify_node({}) == {"next": "act"}
    assert len(model.prompts) == 1
    assert brain._act_node({}) == {"next": "verify"}
    assert relay.actions[1].root.target.image_point == (160, 35)
    assert relay.actions[1].root.target.evidence.startswith("image_refined:")
    assert brain.click_refinement.bound is None
    assert brain._verify_node({}) == {"next": "review"}
    assert relay.changed and len(model.prompts) == 1
    assert any(e.payload.get("check") == "navigation_observed_change" for e in brain.log.read_all())


@pytest.mark.parametrize("mode", ["disabled", "replan", "same_point", "still_no_change"])
def test_no_blind_repeated_click_or_repeated_image_request(tmp_path, record, mode):
    answer = {
        "outcome": "retry",
        "x": 110 if mode == "same_point" else 160,
        "y": 35,
        "explanation": "One correction",
    }
    if mode == "replan":
        answer = {"outcome": "replan", "explanation": "Already selected; do not click again"}
    brain, relay, model = setup(tmp_path, record, answer, success=False, enabled=mode != "disabled")
    brain._act_node({})
    outcome = brain._verify_node({})
    if mode == "still_no_change":
        assert outcome == {"next": "act"}
        brain._act_node({})
        outcome = brain._verify_node({})
    assert outcome == {"next": "observe"}
    assert len(model.prompts) == (0 if mode == "disabled" else 1)
    assert len(relay.actions) == (2 if mode == "still_no_change" else 1)
    assert brain.feedback[-1]["reason"] == "navigation_no_progress"


def test_refined_point_still_requires_fresh_unchanged_capture(tmp_path, record):
    brain, relay, model = setup(
        tmp_path, record, {"outcome": "retry", "x": 160, "y": 35, "explanation": "Correct caption"}
    )
    brain._act_node({})
    assert brain._verify_node({}) == {"next": "act"}
    relay.stale = True
    assert brain._act_node({}) == {"next": "observe"}
    assert len(relay.actions) == 1
    assert brain.click_refinement.bound is None


def test_successful_first_click_has_no_coordinate_model_call(tmp_path, record):
    brain, relay, model = setup(tmp_path, record, {})
    brain._act_node({})
    relay.changed = True
    assert brain._verify_node({}) == {"next": "review"}
    assert not model.prompts


def test_outside_target_coordinate_is_not_dispatched(tmp_path, record):
    brain, relay, _ = setup(
        tmp_path, record, {"outcome": "retry", "x": 350, "y": 150, "explanation": "Invalid target"}
    )
    brain._act_node({})
    assert brain._verify_node({}) == {"next": "finish"}
    assert brain.result.reason == "refined_click_outside_target"
    assert len(relay.actions) == 1
