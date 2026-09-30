"""Business gates consume visual semantics and emit only capture-bound pixels."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sawon_interfaces import (
    Action,
    ActionResult,
    CaptureGeometry,
    ImageTarget,
    Observation,
    Screenshot,
)
from test_brain import FakeRelay, ScriptedModel, success_steps
from test_brain import record as record

from brain.graph.engine import Brain
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings
from brain.visual.scene import SceneObservation


class VisualFixture(FakeRelay):
    def observe(self):
        original = super().observe()
        # Fixture geometry is unique for each target, independent of labels.
        self.items = [
            item.model_copy(update={"bbox": (int(item.ref[1:]) * 10, 10, 8, 20)})
            for item in original.elements
        ]
        data = original.model_dump()
        data.update(
            observation_mode="visual_only",
            elements=[],
            screenshot=Screenshot(
                path="screenshots/fixture.png",
                width=1000,
                height=100,
                geometry=CaptureGeometry(
                    capture_id=uuid4(),
                    captured_at=datetime.now(UTC),
                    origin=(-100, 0),
                    physical_size=(1000, 100),
                    dpi=144,
                ),
            ),
        )
        self.current = Observation.model_validate(data)
        return self.current

    def act(self, action):
        assert isinstance(action.root.target, ImageTarget)
        assert action.root.obs_id == self.current.obs_id
        assert action.root.target.capture_id == self.current.screenshot.geometry.capture_id
        x, y = action.root.target.image_point
        targets = [
            item
            for item in self.items
            if item.bbox[0] <= x < item.bbox[0] + item.bbox[2]
            and item.bbox[1] <= y < item.bbox[1] + item.bbox[3]
        ]
        assert len(targets) == 1
        data = action.model_dump(mode="json")
        data["target"] = {"ref": targets[0].ref}
        result = super().act(Action.model_validate(data))
        self.actions[-1] = action
        return result


class SyntheticPerception:
    """Perception output fixture. No desktop/UIA is accessed in this test."""

    def __init__(self, relay):
        self.relay = relay

    def observe(self, wire):
        return SceneObservation(wire=wire, elements=self.relay.items)


@pytest.mark.parametrize(
    "error", ["screen_changed", "unsupported_key", "PRIVATE unexpected server text"]
)
def test_relay_diagnostic_records_only_known_codes(tmp_path, record, error):
    class RejectedRelay(VisualFixture):
        def act(self, action):
            return ActionResult(
                action_id=action.root.action_id, ok=False, error=error, duration_ms=0
            )

    relay = RejectedRelay(record)
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="visual-test")
    brain = Brain(
        record=record,
        goal="Save for approval",
        relay=relay,
        model=ScriptedModel(log, success_steps(record)),
        settings=BrainSettings(perception_mode="visual_only"),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    brain.visual_perception = SyntheticPerception(relay)
    assert brain.run().status != "success"
    events = [e for e in log.read_all() if e.kind == "act" and e.payload.get("stage") == "returned"]
    assert events and events[0].payload["error_code"] == (
        error if error in {"screen_changed", "unsupported_key"} else None
    )
    assert "PRIVATE" not in (tmp_path / "run_log.jsonl").read_text()


@pytest.mark.parametrize("corrupt", [False, True])
def test_visual_complete_flow_and_corrupt_reread_gate(tmp_path, record, corrupt):
    relay = VisualFixture(record, corrupt=corrupt)
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="visual-test")
    model = ScriptedModel(log, success_steps(record))
    brain = Brain(
        record=record,
        goal="Save for approval",
        relay=relay,
        model=model,
        settings=BrainSettings(perception_mode="visual_only"),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    brain.visual_perception = SyntheticPerception(relay)
    result = brain.run()
    if corrupt:
        assert result.status != "success"
        assert not relay.saved
    else:
        assert result.status == "success", result
        assert relay.saved
        assert len(relay.actions) == 17
        assert all(isinstance(action.root.target, ImageTarget) for action in relay.actions)
        assert any(
            event.payload.get("check") == "visual_identical_capture_renewal"
            for event in log.read_all()
        )


@pytest.mark.parametrize("after_blur", ["correct", "wrong_value", "still_focused", "disabled"])
def test_caret_recheck_uses_fresh_evidence_without_cleaning_values(tmp_path, record, after_blur):
    class CaretRelay(VisualFixture):
        focused_ref = None
        blurred = False

        def act(self, action):
            result = super().act(action)
            if action.root.type in {"set_text", "type_text"} and self.mode == "editor":
                self.focused_ref = "e20"
            elif action.root.type == "key":
                assert action.root.keys == "tab"
                self.blurred = True
                if after_blur != "still_focused":
                    self.focused_ref = None
            return result

    class CaretPerception(SyntheticPerception):
        def observe(self, wire):
            scene = super().observe(wire)
            elements = [
                item.model_copy(
                    update={
                        "focused": self.relay.focused_ref == item.ref,
                        "value": (item.value or "") + "|",
                    }
                )
                if item.ref == "e20"
                and item.value
                and (not self.relay.blurred or after_blur != "correct")
                else item
                for item in scene.elements
            ]
            return scene.model_copy(update={"elements": elements})

    relay = CaretRelay(record)
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="visual-test")
    brain = Brain(
        record=record,
        goal="Save for approval",
        relay=relay,
        model=ScriptedModel(log, success_steps(record)),
        settings=BrainSettings(
            perception_mode="visual_only", visual_input_blur_recheck=after_blur != "disabled"
        ),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    brain.visual_perception = CaretPerception(relay)
    result = brain.run()
    keys = [action for action in relay.actions if action.root.type == "key"]
    assert len(keys) == (0 if after_blur == "disabled" else 1)
    if after_blur == "correct":
        assert result.status == "success", result
        assert relay.saved
        assert len(relay.actions) == 18
    else:
        assert result.status != "success"
        assert not relay.saved
