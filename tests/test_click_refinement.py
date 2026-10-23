"""Real executor regressions: OCR center miss -> one measured image correction."""

from uuid import uuid4

import pytest
from PIL import Image
from sawon_interfaces import ActionResult
from test_brain import ScriptedModel, element
from test_brain import record as record
from test_visual_scene import wire

from brain.graph.click_refinement import DetailedClick
from brain.graph.engine import Brain
from brain.graph.schemas import Choice, Decision
from brain.settings import BrainSettings
from brain.visual.markers import Marker, ground_meanings
from brain.visual.scanning import VisualGroundingError
from brain.visual.scene import SemanticDetection, bind_scene
from pipeline.schemas import OcrBlock
from sawon_runtime.storage import RunLog


def test_detailed_click_bounds_provider_explanation_without_losing_point():
    answer = DetailedClick.model_validate(
        {"outcome": "retry", "x": 10, "y": 20, "explanation": "x" * 500}
    )

    assert answer.x == 10
    assert answer.y == 20
    assert len(answer.explanation) == 300


def setup(
    tmp_path,
    record,
    answer,
    *,
    success=True,
    enabled=True,
    expected_failure="navigation_no_progress",
    intent="select_tab",
    target_role="tabitem",
):
    class Relay:
        changed = False
        actions = []
        stale = False

        def observe(self):
            self.current = wire()
            if self.stale:
                self.current.screen_hash = "b" * 40
            return self.current

        def screenshot(self, _obs, path):
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
        assert expected_failure in prompt
        return generate(prompt, schema, images=images)

    model._generate = capture
    brain = Brain(
        record=record,
        goal="Open the second panel",
        relay=relay,
        model=model,
        settings=BrainSettings(
            screen_matching=False,
            visual_click_refinement=enabled,
        ),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    brain.phase = "enter"
    brain.duplicate_clear = True
    brain.steps = 1

    def observe():
        current = relay.observe()
        # Reproduce the recorded merged-caption response: the model omits the
        # OCR link, which is supplied locally before the real executor runs.
        block = OcrBlock(id=4, text="First Second", bbox=(20, 20, 180, 30), confidence=1)
        marker = Marker(id=8, bbox=block.bbox, source="ocr", text_ids=(4,), confidence=1)
        meanings = SemanticDetection.model_validate(
            {
                "screen_kind": "editor",
                "controls": [{"id": 8, "role": target_role, "text_ids": [], "confidence": 1}],
            }
        )
        scene = bind_scene(current, [block], ground_meanings(meanings, [marker], [block]))
        body = element(
            "e" + str(current.obs_id.int) + "0",
            "text",
            name="Second panel" if relay.changed else "First panel",
        )
        body.bbox = (20, 90, 180, 30)
        brain.observation = scene.model_copy(update={"elements": [*scene.elements, body]})
        return brain.observation

    brain._observe = observe
    observe()
    brain.decision = Decision(
        screen_kind="editor",
        operation="act",
        intent=intent,
        subgoal="switch-panel",
        reason="Open the second panel",
        action=Choice(type="click", ref=brain.observation.elements[0].ref),
    )
    brain.execution_authorized = True
    return brain, relay, model


def test_execution_rejects_a_decision_that_review_did_not_authorize(tmp_path, record):
    brain, relay, _ = setup(tmp_path, record, {})
    before = len(relay.actions)
    brain.execution_authorized = False

    assert brain._act_node() == "finish"
    assert brain.result.reason == "execution_review_required"
    assert len(relay.actions) == before


def test_failed_center_click_refines_once_then_uses_shared_execution_and_verification(
    tmp_path, record
):
    brain, relay, model = setup(
        tmp_path,
        record,
        {"outcome": "retry", "x": 160, "y": 35, "explanation": "Second caption is on the right"},
    )
    assert brain._act_node() == "observation"
    assert brain.pending is not None
    assert not model.prompts  # No image request before a failed attempt.
    assert relay.actions[0].root.target.image_point == (110, 35)
    assert brain._verify_node() == "execution"
    assert len(model.prompts) == 1
    assert brain._act_node() == "observation"
    assert brain.pending is not None
    assert relay.actions[1].root.target.image_point == (160, 35)
    assert relay.actions[1].root.target.evidence.startswith("image_refined:")
    assert brain.click_refinement.bound is None
    assert brain._verify_node() == "review"
    assert relay.changed and len(model.prompts) == 1
    assert any(e.payload.get("check") == "navigation_observed_change" for e in brain.log.read_all())


@pytest.mark.parametrize(
    "mode", ["disabled", "replan", "same_point", "near_center", "still_no_change"]
)
def test_no_blind_repeated_click_or_repeated_image_request(tmp_path, record, mode):
    answer = {
        "outcome": "retry",
        "x": 110 if mode == "same_point" else 160,
        "y": 35,
        "explanation": "One correction",
    }
    if mode == "near_center":
        answer["x"] = 112
    if mode == "replan":
        answer = {"outcome": "replan", "explanation": "Already selected; do not click again"}
    brain, relay, model = setup(tmp_path, record, answer, success=False, enabled=mode != "disabled")
    brain._act_node()
    outcome = brain._verify_node()
    if mode == "still_no_change":
        assert outcome == "execution"
        brain._act_node()
        outcome = brain._verify_node()
    assert outcome == "observation"
    assert len(model.prompts) == (0 if mode == "disabled" else 1)
    assert len(relay.actions) == (2 if mode == "still_no_change" else 1)
    assert brain.feedback[-1]["reason"] == "navigation_no_progress"


def test_merged_tab_refinement_rejects_another_point_in_the_same_region(tmp_path, record):
    brain, relay, model = setup(
        tmp_path,
        record,
        {"outcome": "retry", "x": 145, "y": 35, "explanation": "Still the right caption"},
        success=False,
    )
    brain._act_node()
    assert brain.pending is not None
    brain.pending["delivered_point"] = (180, 35)

    assert brain._verify_node() == "observation"
    assert len(relay.actions) == 1 and len(model.prompts) == 1
    assert any(
        event.payload.get("check") == "refined_click_same_region" for event in brain.log.read_all()
    )


def test_merged_tab_repeat_key_distinguishes_left_and_right_subtargets(tmp_path, record):
    brain, _, _ = setup(tmp_path, record, {})
    brain._act_node()
    assert brain.pending is not None
    right = dict(brain.pending)
    right["delivered_point"] = (180, 35)
    left = dict(brain.pending)
    left["delivered_point"] = (60, 35)
    scope = brain.pending["progress_scope"]

    assert not brain.click_refinement.repeated_target(right, scope)
    assert not brain.click_refinement.repeated_target(left, scope)
    assert brain.click_refinement.repeated_target(left, scope)


def test_refined_point_still_requires_fresh_unchanged_capture(tmp_path, record):
    brain, relay, model = setup(
        tmp_path, record, {"outcome": "retry", "x": 160, "y": 35, "explanation": "Correct caption"}
    )
    brain._act_node()
    assert brain._verify_node() == "execution"
    relay.stale = True
    assert brain._act_node() == "observation"
    assert len(relay.actions) == 1
    assert brain.click_refinement.bound is None


def test_navigation_cycle_requests_detailed_coordinate_inside_merged_tab(tmp_path, record):
    brain, relay, model = setup(
        tmp_path,
        record,
        {"outcome": "retry", "x": 160, "y": 35, "explanation": "Use the right caption"},
        expected_failure="navigation_cycle",
    )
    assert brain._act_node() == "observation"
    assert brain.pending is not None
    pending = brain.pending
    assert pending is not None
    target = brain.observation.elements[0]
    scene = brain.observation.model_copy(
        update={
            "elements": [
                target.model_copy(update={"name": "First  Second", "label_hint": None}),
                *brain.observation.elements[1:],
            ]
        }
    )

    assert brain._try_click_refinement(pending, scene, failure="navigation_cycle")
    assert len(model.prompts) == 1
    assert brain.feedback[-1]["reason"] == "navigation_cycle"
    assert brain.click_refinement.bound.point == (160, 35)
    event = next(
        e for e in brain.log.read_all() if e.payload.get("check") == "click_coordinate_review"
    )
    assert event.payload["failure"] == "navigation_cycle"
    assert event.payload["target_binding"] == "semantic_target_key"


def test_repeated_semantic_click_target_requests_detail_despite_changed_ocr_state(tmp_path, record):
    brain, relay, model = setup(
        tmp_path,
        record,
        {"outcome": "retry", "x": 160, "y": 35, "explanation": "Use the right caption"},
        expected_failure="semantic_target_repeated",
        intent="navigate",
        target_role="button",
    )
    assert brain._act_node() == "observation"
    assert brain.pending is not None
    assert brain.pending is not None
    scope = brain.pending["progress_scope"]
    assert not brain.click_refinement.repeated_target(brain.pending, scope)
    relay.changed = True
    assert brain._verify_node() == "execution"
    assert len(model.prompts) == 1
    assert brain.click_refinement.bound.point == (160, 35)
    assert brain.feedback[-1]["reason"] == "semantic_target_repeated"


def test_successful_first_click_has_no_coordinate_model_call(tmp_path, record):
    brain, relay, model = setup(tmp_path, record, {})
    brain._act_node()
    relay.changed = True
    assert brain._verify_node() == "review"
    assert not model.prompts


def test_outside_target_coordinate_is_not_dispatched(tmp_path, record):
    brain, relay, _ = setup(
        tmp_path, record, {"outcome": "retry", "x": 350, "y": 150, "explanation": "Invalid target"}
    )
    brain._act_node()
    assert brain._verify_node() == "finish"
    assert brain.result.reason == "refined_click_outside_target"
    assert len(relay.actions) == 1


def test_exhausted_grounding_repair_does_not_restart_from_outer_verification(tmp_path, record):
    brain, relay, model = setup(tmp_path, record, {})
    brain._act_node()

    def exhausted():
        raise VisualGroundingError("ocr_marker_text_mismatch")

    brain._observe = exhausted
    assert brain._verify_node() == "finish"
    assert brain.result.reason == "visual_grounding:ocr_marker_text_mismatch"
    assert len(relay.actions) == 1 and not model.prompts


def test_synthetic_no_progress_preserves_current_alternative_plan(tmp_path, record):
    """Synthetic relay: a failed click must not discard the next observed plan."""
    from brain.graph.screen_decision import ScreenMatch

    brain, relay, model = setup(tmp_path, record, {}, enabled=False, success=False)
    brain._act_node()
    observe = brain._observe
    alternative = ScreenMatch(
        screen_kind="editor",
        controls=[],
        procedure_step="alternative",
        steps=[{"target": 8, "action": "click", "intent": "select_tab", "point": [160, 35]}],
    )

    def observe_with_plan():
        observation = observe()
        brain.visual_proposal = alternative
        brain.visual_proposal_obs_id = observation.obs_id
        return observation

    brain._observe = observe_with_plan
    assert brain._verify_node() == "review"
    assert brain.visual_proposal is alternative
    assert brain.pending is None
    assert brain.feedback[-1]["reason"] == "navigation_no_progress"
    assert len(relay.actions) == 1 and not model.prompts


def test_synthetic_repeated_target_preserves_current_alternative_plan(tmp_path, record):
    """Synthetic relay: an old repeated target must not discard the current screen plan."""
    from brain.graph.screen_decision import ScreenMatch

    brain, relay, model = setup(
        tmp_path,
        record,
        {},
        enabled=False,
        intent="navigate",
        target_role="button",
    )
    brain._act_node()
    assert brain.pending is not None
    scope = brain.pending["progress_scope"]
    assert not brain.click_refinement.repeated_target(brain.pending, scope)
    alternative = ScreenMatch(
        screen_kind="query",
        controls=[],
        procedure_step="open-new-record",
        steps=[{"target": 9, "action": "click", "intent": "navigate", "point": [240, 120]}],
    )
    observe = brain._observe

    def observe_with_plan():
        observation = observe()
        brain.visual_proposal = alternative
        brain.visual_proposal_obs_id = observation.obs_id
        return observation

    brain._observe = observe_with_plan
    relay.changed = True

    assert brain._verify_node() == "review"
    assert brain.visual_proposal is alternative
    assert brain.pending is None
    assert brain.feedback[-1]["reason"] == "semantic_target_repeated"
    assert len(relay.actions) == 1 and not model.prompts
