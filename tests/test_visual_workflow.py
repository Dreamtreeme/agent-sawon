"""Business gates consume visual semantics and emit only capture-bound pixels."""

from datetime import UTC, datetime
from uuid import uuid4

from sawon_interfaces import (
    Action,
    CaptureGeometry,
    ImageTarget,
    Observation,
    Screenshot,
)
from test_brain import FakeRelay, ScriptedModel
from test_brain import record as record

from brain.graph.engine import Brain
from brain.graph.schemas import Choice, Decision
from brain.graph.transitions import ObservedEffect
from brain.settings import BrainSettings
from brain.visual.scene import SceneObservation
from sawon_runtime.storage import RunLog


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


def test_relay_error_code_allowlist_rejects_untrusted_text():
    from brain.graph.execution import relay_error_code

    known = {
        "observation_expired",
        "capture_expired",
        "capture_unavailable",
        "capture_geometry_changed",
        "screen_changed",
        "window_changed",
        "foreground_not_allowed",
        "focus_not_confirmed",
        "point_outside_allowed_window",
        "point_outside_capture",
        "observation_stale",
        "observation_blocked",
        "action_failed",
        "unsupported_key",
    }

    assert {error: relay_error_code(error) for error in known} == {error: error for error in known}
    assert relay_error_code(None) is None
    assert relay_error_code("PRIVATE unexpected server text") is None


def test_caret_recheck_failure_rules_are_local_and_independent(tmp_path, monkeypatch, record):
    """The three deterministic rejection branches do not need a full 18-action replay."""
    choice = Choice(type="set_text", ref="e20", source="hbl_no")
    decision = Decision.model_validate(
        {
            "screen_kind": "editor",
            "operation": "act",
            "intent": "fill",
            "subgoal": "Fill the identifier",
            "reason": "The visible field matches the requested input",
            "action": choice,
        }
    )
    expected = record.fields.hbl_no.value
    cases = (
        ("disabled", False, True, expected + "|", False, "input_value_mismatch"),
        ("wrong_value", True, False, expected + "|", True, "input_value_mismatch"),
        ("still_focused", True, True, expected + "|", True, "input_focus_not_moved"),
    )
    for name, enabled, after_focused, after_value, should_blur, failure in cases:
        relay = VisualFixture(record)
        relay.mode = "editor"
        wire = relay.observe()
        base = next(item for item in relay.items if item.ref == "e20")
        root = next(item for item in relay.items if item.ref == "e0")
        initial = base.model_copy(update={"focused": True, "value": expected + "|"})
        scene = SceneObservation(wire=wire, elements=[root, initial])
        after = SceneObservation(
            wire=wire,
            elements=[
                root,
                base.model_copy(update={"focused": after_focused, "value": after_value}),
            ],
        )
        case_dir = tmp_path / name
        log = RunLog(case_dir, case_id=record.case_id, agent_id="visual-test")
        brain = Brain(
            record=record,
            goal="Save for approval",
            relay=relay,
            model=ScriptedModel(log, []),
            settings=BrainSettings(
                screen_matching=False,
                visual_input_blur_recheck=enabled,
            ),
            case_dir=case_dir,
            run_log=log,
            visual_ocr=object(),
        )
        brain.phase = "task"
        brain.decision = decision
        blurred = []
        failures = []
        monkeypatch.setattr(
            "brain.graph.engine.delivered_input_element",
            lambda _pending, observation, _choice, _surfaces: next(
                item for item in observation.elements if item.ref == "e20"
            ),
        )
        monkeypatch.setattr(
            brain,
            "_blur_visual_input",
            lambda *_, _blurred=blurred, _name=name: _blurred.append(_name),
        )
        monkeypatch.setattr(brain, "_observe", lambda _after=after: _after)
        monkeypatch.setattr(
            brain,
            "_fail",
            lambda reason, _failures=failures: _failures.append(reason) or "observation",
        )

        outcome, returned, _effect = brain._verify_text_input(
            {"descriptor": {}}, scene, choice, decision, [], ObservedEffect.NO_CHANGE
        )

        assert bool(blurred) is should_blur, name
        assert failures == [failure], name
        assert outcome == "observation", name
        assert returned is (after if should_blur else scene), name
