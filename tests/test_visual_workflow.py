"""Business gates consume visual semantics and emit only capture-bound pixels."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sawon_interfaces import Action, CaptureGeometry, ImageTarget, Observation, Screenshot
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
