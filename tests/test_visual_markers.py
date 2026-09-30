"""Coordinate invention regression and local detector -> semantic -> scene boundary."""

import json

import pytest
from PIL import Image
from pydantic import ValidationError
from test_visual_scene import wire

from brain.runtime.storage import RunLog
from brain.visual.detector import DetectorSettings, OmniParserDetector
from brain.visual.detector_worker import original_box
from brain.visual.markers import Marker, build_markers, ground_meanings
from brain.visual.perception import VisualPerception
from brain.visual.scene import SemanticDetection, bind_scene
from pipeline.schemas import OcrBlock, RenderedPage


def fixture():
    blocks = [OcrBlock(id=8, text="Reference", bbox=(10, 20, 60, 15), confidence=0.99)]
    boxes = [Marker(id=0, bbox=(100, 15, 150, 30), source="omniparser", confidence=0.4)]
    markers = build_markers(blocks, boxes, 400, 200)
    target = next(m for m in markers if m.source == "omniparser")
    meaning = SemanticDetection.model_validate(
        {
            "controls": [
                {
                    "id": target.id,
                    "role": "edit",
                    "label_ids": [8],
                    "confidence": 0.95,
                }
            ]
        }
    )
    return blocks, boxes, markers, meaning


def test_only_local_geometry_becomes_click_target():
    blocks, _, markers, meaning = fixture()
    scene = bind_scene(wire(), blocks, ground_meanings(meaning, markers, blocks))
    target = next(e for e in scene.elements if e.role == "edit")
    assert target.bbox == (100, 15, 150, 30)
    assert target.name == "Reference" and target.enabled
    assert target.value is None
    assert len(scene.elements) == 2  # unclassified OCR is retained as evidence
    # Capture transform, including negative monitor origin; no extra DPI factor.
    assert scene.screenshot.desktop_point((175, 30)) == (-50, 60)


def test_semantic_schema_cannot_generate_coordinates():
    schema = json.dumps(SemanticDetection.model_json_schema())
    assert "bbox" not in schema and "prefixItems" not in schema
    with pytest.raises(ValidationError, match="Extra inputs"):
        SemanticDetection.model_validate(
            {
                "controls": [
                    {
                        "id": 3,
                        "role": "button",
                        "confidence": 0.9,
                        "bbox": [778, 15, 24, 22],
                    }
                ]
            }
        )


@pytest.mark.parametrize("ids", [[999], [0, 0]])
def test_unknown_or_duplicate_marker_cannot_be_bound(ids):
    blocks, _, markers, _ = fixture()
    meaning = SemanticDetection.model_validate(
        {"controls": [{"id": i, "role": "button", "confidence": 1} for i in ids]}
    )
    with pytest.raises(ValueError, match="unknown_or_duplicate"):
        ground_meanings(meaning, markers, blocks)


def test_label_is_never_promoted_to_input():
    blocks, _, markers, _ = fixture()
    label = next(m for m in markers if m.source == "ocr")
    meaning = SemanticDetection.model_validate(
        {
            "controls": [
                {
                    "id": label.id,
                    "role": "edit",
                    "confidence": 1,
                    "text_ids": [8],
                }
            ]
        }
    )
    with pytest.raises(ValueError, match="ocr_text_is_not"):
        ground_meanings(meaning, markers, blocks)


def test_resize_rounding_is_inverted_per_axis():
    assert original_box([0, 0, 640, 317], (1001, 497), (640, 317)) == [0, 0, 1001, 497]
    assert original_box([64, 31.7, 128, 63.4], (1001, 497), (640, 317)) == [100, 49, 101, 51]
    with pytest.raises(ValueError, match="outside"):
        original_box([676, 15, 699, 37], (622, 476), (622, 476))


def test_merge_removes_glyphs_duplicates_but_retains_enclosing_input():
    blocks = [OcrBlock(id=1, text="Value", bbox=(20, 20, 60, 20), confidence=1)]
    boxes = [
        Marker(id=i, bbox=b, source="omniparser", confidence=0.9 - i / 10)
        for i, b in enumerate([(10, 10, 100, 50), (11, 10, 100, 50), (20, 20, 10, 20)])
    ]
    markers = build_markers(blocks, boxes, 400, 200)
    assert len(markers) == 2
    assert {m.bbox for m in markers} == {(10, 10, 100, 50), (20, 20, 60, 20)}
    with pytest.raises(ValueError, match="outside"):
        build_markers([], [boxes[0].model_copy(update={"bbox": (778, 15, 24, 22)})], 622, 476)


def test_missing_weights_fail_without_paid_fallback(tmp_path):
    page = RenderedPage(
        document="test", kind="hbl", page=1, image_path=tmp_path / "x.png", width=400, height=200
    )
    Image.new("RGB", (400, 200)).save(page.image_path)
    log = RunLog(tmp_path, case_id="detector", agent_id="test")
    detector = OmniParserDetector(DetectorSettings(weights=tmp_path / "missing.pt"), log)
    with pytest.raises(ValueError, match="weights_missing"):
        detector.read(page)
    calls = [e for e in log.read_all() if e.kind == "model_call"]
    assert len(calls) == 1 and calls[0].cost.usd == 0
    assert calls[0].payload["usage_source"] == "local_no_tokens"


def test_complete_perception_uses_marked_image_and_preserves_raw_capture(tmp_path):
    blocks, boxes, _, meaning = fixture()

    class Relay:
        def screenshot(self, observation, path):
            path.parent.mkdir(parents=True)
            Image.new("RGB", (400, 200), "white").save(path)
            return path

    class Ocr:
        def read(self, page):
            assert page.image_path.name == "capture.png"
            return blocks

    class Detector:
        def read(self, page):
            assert page.image_path.name == "capture.png"
            return boxes

    calls = []

    def interpret(prompt, schema, page):
        assert schema is SemanticDetection
        assert page.image_path.name == "markers.png"
        assert "markers" in prompt
        calls.append(page)
        return meaning

    log = RunLog(tmp_path, case_id="markers", agent_id="test")
    perception = VisualPerception(Relay(), Ocr(), tmp_path, log, interpret, detector=Detector())
    scene = perception.observe(wire())
    assert len(calls) == 1 and any(e.role == "edit" for e in scene.elements)
    folder = calls[0].image_path.parent
    with Image.open(folder / "capture.png") as image:
        assert image.getpixel((100, 15)) == (255, 255, 255)
    assert (folder / "markers.json").is_file() and (folder / "meanings.json").is_file()


def test_settings_resolve_local_weights_relative_to_config(tmp_path):
    from brain.settings import BrainSettings

    config = tmp_path / "brain.toml"
    config.write_text('[visual_detector]\nweights="weights/model.pt"', encoding="utf-8")
    assert BrainSettings.load(config).visual_detector.weights == tmp_path / "weights/model.pt"
