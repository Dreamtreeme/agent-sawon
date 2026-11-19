"""Fresh captures preserve a chosen point or return the screen for a model decision."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw
from sawon_interfaces import CaptureGeometry, Observation, Screenshot, UIElement, WindowInfo

from brain.graph.action_preparation import (
    ActionWiringRequest,
    ActionWiringServices,
    ScreenDecisionRequired,
    wire_action,
)
from brain.graph.schemas import Choice, Decision
from brain.visual.action_rebind import rebind_input_point
from brain.visual.markers import Marker
from brain.visual.scanning import ScreenScanner
from brain.visual.scene import SceneObservation
from pipeline.schemas import OcrBlock
from sawon_runtime.storage import RunLog


def wire(width=400, height=200):
    window = WindowInfo(handle=7, title="ERP", process="erp.exe")
    return Observation(
        obs_id=uuid4(),
        ts=datetime.now(UTC),
        active_window=window,
        windows=[window],
        elements=[],
        observation_mode="visual_only",
        screen_hash="a" * 40,
        screenshot=Screenshot(
            path="screenshots/current.png",
            width=width,
            height=height,
            geometry=CaptureGeometry(
                capture_id=uuid4(),
                captured_at=datetime.now(UTC),
                origin=(0.0, 0.0),
                physical_size=(width, height),
                dpi=144,
            ),
        ),
    )


def same_form_images(tmp_path):
    old = Image.new("RGB", (400, 200), "white")
    draw = ImageDraw.Draw(old)
    draw.rectangle((100, 40, 299, 69), outline="black", width=2)
    draw.rectangle((100, 100, 179, 129), outline="black", width=2)
    old_path = tmp_path / "old.png"
    fresh_path = tmp_path / "fresh.png"
    old.save(old_path)
    old.resize((600, 240), Image.Resampling.LANCZOS).save(fresh_path)
    return old_path, fresh_path


@pytest.mark.parametrize(
    "role,observed_box,other_boxes",
    [
        ("edit", (100, 40, 200, 30), [(130, 35, 155, 74)]),
        ("combobox", (100, 40, 200, 30), []),
        ("edit", (170, 55, 1, 1), []),
        ("edit", (10, 40, 65, 30), []),
    ],
    ids=["overlapping-controls", "combobox", "point-only-fill", "point-outside-observed-label"],
)
def test_resized_input_preserves_model_selected_point(role, observed_box, other_boxes):
    target = UIElement(ref="e2", role=role, name="House B/L 번호", bbox=observed_box)
    scene = SceneObservation(wire=wire(), elements=[target])
    fresh_box = (150, 48, 300, 36)
    result = rebind_input_point(
        scene,
        target,
        fresh_size=(600, 240),
        markers=[
            Marker(id=i, bbox=box, source="omniparser", confidence=1)
            for i, box in enumerate([*other_boxes, fresh_box])
        ],
        planned_point=(170, 55),
    )
    assert result.bbox == fresh_box
    assert result.point == (255, 66)


@pytest.mark.parametrize(
    ("boxes", "error"),
    [
        ([], "visual_input_control_not_found"),
        ([(100, 90, 200, 30)], "visual_input_control_not_found"),
        ([(50, 40, 200, 30), (150, 40, 200, 30)], "visual_input_control_ambiguous"),
    ],
    ids=["input-disappeared", "only-next-field-remains", "equally-plausible-inputs"],
)
def test_input_rebinding_requires_a_current_control(boxes, error):
    target = UIElement(ref="e2", role="edit", name="수량", bbox=(100, 40, 200, 30))
    scene = SceneObservation(wire=wire(), elements=[target])
    with pytest.raises(ValueError, match=error):
        rebind_input_point(
            scene,
            target,
            fresh_size=(400, 200),
            markers=[
                Marker(id=i, bbox=box, source="omniparser", confidence=1)
                for i, box in enumerate(boxes)
            ],
        )


@pytest.mark.parametrize(
    "role,choice_data",
    [
        ("button", {"type": "click"}),
        ("edit", {"type": "double_click"}),
        ("table", {"type": "key", "keys": "enter"}),
        ("cell", {"type": "set_text", "source": "quantity"}),
    ],
)
def test_changed_capture_hands_old_target_back_for_a_model_decision(tmp_path, role, choice_data):
    target = UIElement(ref="e2", role=role, name="저장", bbox=(100, 100, 80, 30))
    scene = SceneObservation(wire=wire(), elements=[target])
    fresh = wire().model_copy(update={"screen_hash": "c" * 40})
    choice = Choice(ref=target.ref, **choice_data)
    decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="navigate",
        subgoal="Continue",
        reason="Use the selected target",
        action=choice,
    )
    request = ActionWiringRequest(
        scene,
        choice,
        decision,
        {},
        {"quantity": "1"},
        planned_point=(120, 115),
        fresh_observation=fresh,
    )

    def unexpected_local_rebind(*args, **kwargs):
        pytest.fail("A local selector received the model's click target")

    services = ActionWiringServices(
        None, RunLog(tmp_path, case_id="rebind", agent_id="test"), unexpected_local_rebind
    )
    with pytest.raises(ScreenDecisionRequired) as captured:
        wire_action(request, services)
    assert captured.value.observation is fresh

    same_frame = fresh.model_copy(update={"screen_hash": scene.screen_hash})
    new_action = wire_action(replace(request, fresh_observation=same_frame), services).root
    assert new_action.type == choice.type
    assert new_action.target.image_point == (120, 115)
    assert new_action.target.capture_id == same_frame.screenshot.geometry.capture_id


def test_perception_reuses_one_local_catalog_and_never_calls_semantic_model(tmp_path):
    old_path, fresh_path = same_form_images(tmp_path)
    target = UIElement(
        ref="e2", role="edit", name="사용자", label_hint="사용자", bbox=(100, 40, 200, 30)
    )
    label = UIElement(ref="e1", role="text", name="사용자", bbox=(10, 40, 70, 20))
    scene = SceneObservation(wire=wire(), elements=[label, target])
    planned = tmp_path / "visual" / str(scene.obs_id) / "capture.png"
    planned.parent.mkdir(parents=True)
    planned.write_bytes(old_path.read_bytes())
    calls = {"ocr": 0, "detector": 0}

    class Relay:
        def screenshot(self, observation, path):
            path.parent.mkdir(parents=True)
            path.write_bytes(fresh_path.read_bytes())
            return path

    class Ocr:
        def read(self, page):
            calls["ocr"] += 1
            return [OcrBlock(id=0, text="사용자", confidence=1, bbox=(15, 48, 105, 24))]

    class Detector:
        def read(self, page):
            calls["detector"] += 1
            return [Marker(id=0, bbox=(150, 48, 300, 36), source="omniparser", confidence=1)]

    def semantic_model(_request):
        raise AssertionError("execution-time rebinding must remain local")

    perception = ScreenScanner(
        Relay(),
        Ocr(),
        tmp_path,
        RunLog(tmp_path, case_id="rebind", agent_id="test"),
        semantic_model,
        detector=Detector(),
        model_led_semantics=True,
    )
    fresh = wire(600, 240)
    first = perception.rebind_action(scene, target, fresh)
    second = perception.rebind_action(scene, target, fresh)
    assert first == second
    assert calls == {"ocr": 1, "detector": 1}
