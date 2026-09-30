"""A screen proposal reuses the initial plan, then passes existing state gates."""

import json
from decimal import Decimal

import pytest
from PIL import Image
from test_brain import decision, text_choice
from test_brain import record as record
from test_visual_markers import fixture
from test_visual_scene import wire

from brain.graph.engine import Brain
from brain.graph.visual_turn import VisualTurn, adapt_proposal, bind_proposal
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings
from brain.visual.scene import SemanticDetection
from models.base import MeasuredModelClient, ProviderReply, TokenUsage


@pytest.mark.parametrize("stale_proposal", [False, True])
def test_screen_and_action_share_call_but_stale_proposal_is_replanned(
    tmp_path, record, stale_proposal
):
    blocks, boxes, _, meaning = fixture()
    marker = meaning.controls[0].id

    class Relay:
        def observe(self):
            self.current = wire()
            return self.current

        def screenshot(self, observed, path):
            path.parent.mkdir(parents=True)
            Image.new("RGB", (400, 200), "white").save(path)
            return path

    class Ocr:
        def read(self, page):
            return blocks

    class Detector:
        def read(self, page):
            return boxes

    log = RunLog(tmp_path, case_id=record.case_id, agent_id="test")
    relay = Relay()

    class Model(MeasuredModelClient):
        calls = 0

        def _cost(self, usage):
            return Decimal(0)

        def _generate(self, prompt, schema, *, images=None):
            self.calls += 1
            if schema is VisualTurn:
                assert '"goal": "Register shipment"' in prompt
                assert '"plan": ["Query duplicates", "Enter", "Verify save"]' in prompt
                assert images and "UNTRUSTED_SCREEN=" in prompt
                response = {
                    **meaning.model_dump(),
                    "next_step": {
                        "decision": decision(
                            intent="query_input", action=text_choice("hbl_no", f"m{marker}")
                        )
                    },
                }
            else:
                assert stale_proposal
                ref = f"e{relay.current.obs_id.int}0{marker}"
                response = {
                    "decision": decision(intent="query_input", action=text_choice("hbl_no", ref))
                }
            return ProviderReply(
                text=json.dumps(response), usage=TokenUsage(input_tokens=1, response_tokens=1)
            )

    model = Model(provider="fake", model="fixture", run_log=log)
    brain = Brain(
        record=record,
        goal="Register shipment",
        relay=relay,
        model=model,
        settings=BrainSettings(perception_mode="visual_only", visual_joint_planning=True),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=Ocr(),
    )
    brain.plan = ["Query duplicates", "Enter", "Verify save"]
    brain.visual_perception.detector = Detector()
    brain._observe()
    assert model.calls == 1
    if stale_proposal:
        brain.visual_proposal_obs_id = None
    assert brain._select_node({}) == {"next": "act"}
    assert brain.decision.action.source == "hbl_no"
    assert model.calls == (2 if stale_proposal else 1)
    assert brain.decision.action.ref.startswith(f"e{relay.current.obs_id.int}0")


def test_current_policy_rejects_wrong_phase_action_and_unknown_marker():
    from brain.graph.policy import DecisionPolicy, ExecutionFacts
    from brain.graph.schemas import DecisionResponse

    proposal = DecisionResponse.model_validate(
        {"decision": decision(intent="query_input", action=text_choice("hbl_no", "m5"))}
    )
    with pytest.raises(ValueError, match="unknown_marker"):
        bind_proposal(proposal, "e10", {"e106"})
    bound = bind_proposal(proposal, "e10", {"e105"})
    policy = DecisionPolicy(
        ExecutionFacts(phase="enter", duplicate_clear=True, field_names=("hbl_no",))
    )
    with pytest.raises(ValueError):
        adapt_proposal(bound, policy.response_schema())
    # Metadata does not turn local semantic detection into a persistence claim.
    assert "next_step" not in SemanticDetection.model_fields


@pytest.mark.parametrize("change", ["origin", "dpi", "size", "content"])
def test_translation_rebinds_capture_without_recognition_but_other_changes_do_not(
    tmp_path, record, change
):
    from test_screen_batch import ChangingPixelsRelay, make_brain

    from brain.graph.schemas import Decision

    relay = ChangingPixelsRelay(record)
    brain, perception, model = make_brain(tmp_path, record, relay, [])
    previous = brain._observe()
    original_observe = relay.observe

    def changed_capture():
        observed = original_observe()
        shot = observed.screenshot
        geometry = shot.geometry
        if change == "origin":
            geometry = geometry.model_copy(update={"origin": (200, 300)})
        elif change == "dpi":
            geometry = geometry.model_copy(update={"dpi": 192})
        elif change == "size":
            geometry = geometry.model_copy(update={"physical_size": (2000, 200)})
        shot = shot.model_copy(update={"geometry": geometry})
        updated = {"screenshot": shot}
        if change == "content":
            updated["screen_hash"] = "c" * 40
        relay.current = observed.model_copy(update=updated)
        return relay.current

    relay.observe = changed_capture
    selected = Decision.model_validate(
        decision(intent="query_input", action=text_choice("hbl_no", "e1"))
    )
    if change == "origin":
        action = brain._wire_action(selected.action, selected)
        assert action.root.obs_id != previous.obs_id
        assert action.root.target.capture_id == relay.current.screenshot.geometry.capture_id
        assert action.root.target.image_point == (14, 20)
        assert relay.current.screenshot.desktop_point(action.root.target.image_point) == (214, 320)
    else:
        with pytest.raises(ValueError, match="visual_screen_changed_before_action"):
            brain._wire_action(selected.action, selected)
    assert len(perception.action_counts) == 1
    assert not model.prompts  # No OCR, semantic model or action replanning on translation.
