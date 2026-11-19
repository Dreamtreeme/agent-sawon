"""Execution-time target rebinding uses fresh local visual evidence."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw
from sawon_interfaces import CaptureGeometry, Observation, Screenshot, UIElement, WindowInfo

from brain.visual.action_rebind import rebind_action_target
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
    ("role", "observed_box", "readings", "other_boxes"),
    [
        (
            "edit",
            (100, 40, 200, 30),
            [
                ("House B/L", (15, 42, 82, 18)),
                ("번호", (15, 60, 40, 18)),
                ("House B/L", (150, 120, 82, 18)),
            ],
            [(130, 35, 155, 74)],
        ),
        ("combobox", (100, 40, 200, 30), [], []),
        ("edit", (170, 55, 1, 1), [("House B/L 번호", (15, 48, 80, 24))], []),
        ("edit", (10, 40, 65, 30), [("House B/L 번호", (15, 48, 80, 24))], []),
    ],
    ids=[
        "split-caption-table-header-and-overlapping-controls",
        "no-visible-caption",
        "point-only-fill",
        "point-outside-observed-label",
    ],
)
def test_resized_input_preserves_model_selected_point(role, observed_box, readings, other_boxes):
    target = UIElement(ref="e2", role=role, name="House B/L 번호", bbox=observed_box)
    scene = SceneObservation(wire=wire(), elements=[target])
    fresh_box = (150, 48, 300, 36)
    result = rebind_action_target(
        scene,
        target,
        fresh_size=(600, 240),
        blocks=[
            OcrBlock(id=i, text=text, confidence=1, bbox=bbox)
            for i, (text, bbox) in enumerate(readings)
        ],
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
def test_input_rebinding_requires_a_current_control_even_when_its_caption_remains(boxes, error):
    target = UIElement(ref="e2", role="edit", name="수량", bbox=(100, 40, 200, 30))
    scene = SceneObservation(wire=wire(), elements=[target])
    with pytest.raises(ValueError, match=error):
        rebind_action_target(
            scene,
            target,
            fresh_size=(400, 200),
            blocks=[OcrBlock(id=0, text="수량", confidence=1, bbox=(10, 40, 70, 20))],
            markers=[
                Marker(id=i, bbox=box, source="omniparser", confidence=1)
                for i, box in enumerate(boxes)
            ],
        )


@pytest.mark.parametrize(
    "caption,planned_point,expected",
    [
        ((150, 120, 120, 36), (120, 115), (180, 138)),
        ((100, 100, 80, 30), (200, 150), (200, 150)),
        ((140, 120, 80, 30), (200, 150), (240, 170)),
        ((150, 120, 120, 36), (200, 150), (300, 180)),
    ],
    ids=["inside-caption", "outside-unchanged", "outside-moved", "outside-scaled"],
)
def test_click_rebinds_to_current_ocr_caption_and_preserves_relative_point(
    caption, planned_point, expected
):
    target = UIElement(ref="e2", role="button", name="저장", bbox=(100, 100, 80, 30))
    scene = SceneObservation(wire=wire(), elements=[target])
    result = rebind_action_target(
        scene,
        target,
        fresh_size=(600, 240),
        blocks=[OcrBlock(id=0, text="저장", confidence=1, bbox=caption)],
        markers=[],
        planned_point=planned_point,
    )
    assert result.method == "ocr_text_anchor"
    assert result.point == expected


@pytest.mark.parametrize(
    "intended,visible,fresh_x,expected",
    [("새 건 등록", "새건등록", 180, (220, 115)), ("예", "확인", 100, None)],
    ids=["moved-register-button", "confirmation-replaced-yes"],
)
def test_rebind_on_same_size_frames_uses_current_caption(
    tmp_path, intended, visible, fresh_x, expected
):
    target = UIElement(ref="e2", role="button", name=intended, bbox=(100, 100, 80, 30))
    scene = SceneObservation(wire=wire(), elements=[target])
    planned = tmp_path / "visual" / str(scene.obs_id) / "capture.png"
    planned.parent.mkdir(parents=True)
    old_image = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(old_image).rectangle((100, 100, 179, 129), outline="black")
    old_image.save(planned)
    log = RunLog(tmp_path, case_id="rejected-pair", agent_id="test")
    fresh_path = tmp_path / "fresh.png"
    fresh_image = Image.new("RGB", (400, 200), "white")
    draw = ImageDraw.Draw(fresh_image)
    draw.rectangle((fresh_x, 100, fresh_x + 79, 129), outline="black")
    if expected is not None:
        draw.rectangle((0, 0, 399, 94), fill="black")
    else:
        draw.text((fresh_x + 5, 105), "OK", fill="black")
    fresh_image.save(fresh_path)

    class Relay:
        def screenshot(self, observation, path):
            path.parent.mkdir(parents=True)
            path.write_bytes(fresh_path.read_bytes())
            return path

    class Ocr:
        def read(self, page):
            assert page.image_path.read_bytes() == fresh_path.read_bytes()
            return [OcrBlock(id=0, text=visible, confidence=1, bbox=(fresh_x, 100, 80, 30))]

    class Detector:
        def read(self, page):
            return []

    perception = ScreenScanner(
        Relay(), Ocr(), tmp_path, log, None, detector=Detector(), model_led_semantics=True
    )
    fresh = wire()
    if expected is not None:
        assert perception.rebind_action(scene, target, fresh).point == expected
        return
    with pytest.raises(ValueError, match="^visual_target_anchor_missing$"):
        perception.rebind_action(scene, target, fresh)
    event = log.read_all()[-1]
    preserved = json.loads(Path(event.payload["artifact"]).read_text(encoding="utf-8"))
    assert [Path(item["copy"]).read_bytes() for item in preserved["captures"]] == [
        planned.read_bytes(),
        fresh_path.read_bytes(),
    ]


def test_duplicate_caption_does_not_guess_a_coordinate():
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
