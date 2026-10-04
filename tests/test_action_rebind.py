"""Execution-time target rebinding uses fresh local visual evidence."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw
from sawon_interfaces import CaptureGeometry, Observation, Screenshot, UIElement, WindowInfo

from brain.runtime.storage import RunLog
from brain.visual.action_rebind import hash_distance, perceptual_hash, rebind_action_target
from brain.visual.markers import Marker
from brain.visual.perception import VisualPerception
from brain.visual.scene import SceneObservation
from pipeline.schemas import OcrBlock


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


def test_input_rebinds_to_resized_local_control_without_a_model(tmp_path):
    old_path, fresh_path = same_form_images(tmp_path)
    label = UIElement(ref="e1", role="text", name="사용자", bbox=(10, 40, 70, 20))
    target = UIElement(
        ref="e2", role="edit", name="사용자", label_hint="사용자", bbox=(100, 40, 200, 30)
    )
    scene = SceneObservation(wire=wire(), elements=[label, target])
    result = rebind_action_target(
        scene,
        target,
        old_capture=old_path,
        fresh_capture=fresh_path,
        fresh_size=(600, 240),
        blocks=[OcrBlock(id=0, text="사용자", confidence=1, bbox=(15, 48, 105, 24))],
        markers=[Marker(id=0, bbox=(150, 48, 300, 36), source="omniparser", confidence=1)],
    )
    assert result.method == "ocr_label_local_control"
    assert result.point == (300, 66)
    assert result.phash_distance <= 16


def test_click_rebinds_to_current_ocr_caption_and_preserves_relative_point(tmp_path):
    old_path, fresh_path = same_form_images(tmp_path)
    target = UIElement(ref="e2", role="button", name="저장", bbox=(100, 100, 80, 30))
    scene = SceneObservation(wire=wire(), elements=[target])
    result = rebind_action_target(
        scene,
        target,
        old_capture=old_path,
        fresh_capture=fresh_path,
        fresh_size=(600, 240),
        blocks=[OcrBlock(id=0, text="저장", confidence=1, bbox=(150, 120, 120, 36))],
        markers=[],
        planned_point=(120, 115),
    )
    assert result.method == "ocr_text_anchor"
    assert result.point == (180, 138)


def test_changed_scene_family_is_rejected_before_reusing_visible_caption(tmp_path):
    old_path, fresh_path = same_form_images(tmp_path)
    with Image.open(fresh_path) as source:
        changed = source.copy()
    ImageDraw.Draw(changed).rectangle((0, 0, 599, 150), fill="black")
    changed.save(fresh_path)
    distance = hash_distance(perceptual_hash(old_path), perceptual_hash(fresh_path))
    assert distance > 16
    target = UIElement(ref="e2", role="button", name="저장", bbox=(100, 100, 80, 30))
    scene = SceneObservation(wire=wire(), elements=[target])
    with pytest.raises(ValueError, match="visual_scene_family_changed"):
        rebind_action_target(
            scene,
            target,
            old_capture=old_path,
            fresh_capture=fresh_path,
            fresh_size=(600, 240),
            blocks=[OcrBlock(id=0, text="저장", confidence=1, bbox=(150, 120, 120, 36))],
            markers=[],
        )


def test_duplicate_caption_does_not_guess_a_coordinate(tmp_path):
    old_path, fresh_path = same_form_images(tmp_path)
    target = UIElement(ref="e2", role="button", name="상세", bbox=(100, 100, 80, 30))
    scene = SceneObservation(wire=wire(), elements=[target])
    blocks = [
        OcrBlock(id=0, text="상세", confidence=1, bbox=(150, 120, 80, 30)),
        OcrBlock(id=1, text="상세", confidence=1, bbox=(350, 120, 80, 30)),
    ]
    with pytest.raises(ValueError, match="visual_target_anchor_ambiguous"):
        rebind_action_target(
            scene,
            target,
            old_capture=old_path,
            fresh_capture=fresh_path,
            fresh_size=(600, 240),
            blocks=blocks,
            markers=[],
        )


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

    perception = VisualPerception(
        Relay(),
        Ocr(),
        tmp_path,
        RunLog(tmp_path, case_id="rebind", agent_id="test"),
        semantic_model,
        detector=Detector(),
    )
    fresh = wire(600, 240)
    first = perception.rebind_action(scene, target, fresh)
    second = perception.rebind_action(scene, target, fresh)
    assert first == second
    assert calls == {"ocr": 1, "detector": 1}
