"""The demonstration recorder observes named fields through the measured model boundary."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from sawon_interfaces import CaptureGeometry, Observation, Screenshot, WindowInfo

from brain.graph.screen_mapping import ScreenMappingWire
from brain.settings import BrainSettings
from brain.visual import record
from brain.visual.markers import Marker
from brain.visual.scene import SceneObservation
from models.base import MeasuredModelClient, ProviderReply, TokenUsage
from sawon_runtime.visual import OcrBlock


def recorder_fixture(monkeypatch, tmp_path, *, fields=None):
    window = WindowInfo(handle=1, title="Synthetic", process="fixture.exe")
    observation = Observation(
        obs_id=uuid4(),
        ts=datetime.now(UTC),
        observation_mode="visual_only",
        active_window=window,
        windows=[window],
        elements=[],
        screen_hash="a" * 40,
        screenshot=Screenshot(
            path="screenshots/test.png",
            width=400,
            height=200,
            geometry=CaptureGeometry(
                capture_id=uuid4(),
                captured_at=datetime.now(UTC),
                origin=(0, 0),
                physical_size=(400, 200),
                dpi=96,
            ),
        ),
    )
    calls, reservations, closed = [], [], []

    class Relay:
        def health(self):
            return {"observation_only": True, "observation_mode": "visual_only"}

        def observe(self):
            return observation.model_copy(update={"obs_id": uuid4()})

        def screenshot(self, _wire, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (400, 200), "white").save(path)
            return path

        def act(self, *_args, **_kwargs):
            raise AssertionError("The recorder must not send actions")

    class Ocr:
        def read(self, _page):
            return [
                OcrBlock(id=7, text="Reference", bbox=(10, 25, 70, 15), confidence=1),
                OcrBlock(id=8, text="OCR reading", bbox=(110, 25, 90, 15), confidence=1),
            ]

        def close(self):
            closed.append("ocr")

    class Detector:
        def read(self, _page):
            return [Marker(id=42, source="omniparser", bbox=(100, 20, 200, 30), confidence=1)]

        def close(self):
            closed.append("detector")

    class Model(MeasuredModelClient):
        def _generate(self, prompt, schema, *, images=None):
            calls.append((prompt, schema, images))
            return ProviderReply(
                text=json.dumps(
                    {
                        "screen_kind": "editor",
                        "screen_path": ["Synthetic"],
                        "fields": fields
                        if fields is not None
                        else [
                            {
                                "name": "Reference",
                                "role": "edit",
                                "bbox": [100, 20, 200, 30],
                                "value": "Current visual reading",
                            }
                        ],
                        "steps": [
                            {
                                "action": "click",
                                "target": "Not an observed field",
                                "intent": "navigate",
                                "point": [350, 100],
                            }
                        ],
                    }
                ),
                usage=TokenUsage(input_tokens=1, response_tokens=1),
            )

        def _cost(self, _usage):
            return Decimal("0.2")

    def budget(prompt, schema, page):
        reservations.append((prompt, schema, page))
        return Decimal("0.2")

    monkeypatch.setattr(record, "HttpRelay", lambda *_args: Relay())
    monkeypatch.setattr(record, "PaddleOcrEngine", lambda _settings: Ocr())
    monkeypatch.setattr(record, "OmniParserDetector", lambda *_args: Detector())
    monkeypatch.setattr(
        record.BrainSettings, "load", lambda _path: BrainSettings(screen_matching=False)
    )
    monkeypatch.setattr(record.PipelineSettings, "load", lambda _path: None)
    monkeypatch.setattr(record.ImageCallBudget, "from_config", lambda _path: budget)
    monkeypatch.setattr(
        record,
        "create_model_client",
        lambda _config, *, run_log, env_path: Model(
            provider="fixture", model="synthetic", run_log=run_log
        ),
    )
    output = tmp_path / "recording"
    args = [
        "--relay-url",
        "http://unused.invalid",
        "--output",
        str(output),
        "--ocr-config",
        "unused.toml",
        "--model-config",
        "unused.toml",
        "--brain-config",
        "unused.toml",
        "--max-cost-usd",
        "0.3",
    ]
    return SimpleNamespace(
        args=args, output=output, calls=calls, reservations=reservations, closed=closed
    )


def test_recorder_maps_named_readings_without_numeric_model_references(monkeypatch, tmp_path):
    fixture = recorder_fixture(monkeypatch, tmp_path)

    assert record.main([*fixture.args, "--samples", "1"]) == 0

    assert len(fixture.calls) == 1
    prompt, schema, images = fixture.calls[0]
    assert schema is ScreenMappingWire
    assert images[0].name == "capture.png"
    assert "Reference" in prompt and "OCR reading" in prompt
    for private_key in ('"id"', '"label_ids"', '"text_ids"', '"markers"'):
        assert private_key not in prompt
    reserved_prompt, reserved_schema, page = fixture.reservations[0]
    assert (reserved_prompt, reserved_schema, page.image_path) == (prompt, schema, images[0])
    scene = SceneObservation.model_validate_json(
        (fixture.output / "observations.jsonl").read_text(encoding="utf-8")
    )
    field = next(item for item in scene.elements if item.role == "edit")
    assert (field.name, field.value, field.bbox) == (
        "Reference",
        "Current visual reading",
        (100, 20, 200, 30),
    )
    events = [
        json.loads(line)
        for line in (fixture.output / "run_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    call = next(event for event in events if event["kind"] == "model_call")
    assert call["payload"]["response_schema"] == "ScreenMappingWire"
    assert call["cost"]["usd"] == 0.2
    assert fixture.closed == ["ocr", "detector"]


def test_recorder_named_observations_keep_the_existing_cost_cap(monkeypatch, tmp_path):
    fixture = recorder_fixture(monkeypatch, tmp_path)

    assert record.main([*fixture.args, "--samples", "2"]) == 1

    assert len(fixture.calls) == 1
    assert len(fixture.reservations) == 2
    assert len((fixture.output / "observations.jsonl").read_text().splitlines()) == 1
    result = json.loads((fixture.output / "recording-result.json").read_text())
    assert result == {"status": "stopped", "error_type": "ValueError"}
    assert fixture.closed == ["ocr", "detector"]


@pytest.mark.parametrize("table_state", ["empty", "rows", "uncertain"])
def test_recorder_preserves_named_table_state_parents_and_focus(monkeypatch, tmp_path, table_state):
    # Synthetic named observations traverse the real recorder and scene grounding.
    # Children intentionally precede their parents in the provider response.
    fields = [
        {
            "name": "Reference",
            "role": "edit",
            "bbox": [100, 20, 200, 30],
            "parent": "Window",
            "focused": True,
            "value": "Current visual reading",
        },
        {
            "name": "Continue",
            "role": "button",
            "bbox": [310, 20, 80, 30],
            "parent": "Unobserved parent",
        },
    ]
    if table_state == "rows":
        fields.extend(
            [
                {
                    "name": "Result value",
                    "role": "cell",
                    "bbox": [30, 110, 80, 20],
                    "parent": "Result row",
                    "value": "Visible result",
                },
                {
                    "name": "Result row",
                    "role": "row",
                    "bbox": [20, 100, 360, 40],
                    "parent": "Results",
                },
            ]
        )
    fields.extend(
        [
            {
                "name": "Results",
                "role": "table",
                "bbox": [10, 80, 380, 100],
                "table_state": table_state,
                "parent": "Window",
            },
            {"name": "Window", "role": "window", "bbox": [0, 0, 400, 200]},
        ]
    )
    fixture = recorder_fixture(monkeypatch, tmp_path, fields=fields)

    assert record.main([*fixture.args, "--samples", "1"]) == 0

    assert len(fixture.calls) == 1
    meanings = json.loads(
        next(fixture.output.glob("visual/*/meanings-attempt-0.json")).read_text(encoding="utf-8")
    )
    controls = {control["visual_description"]: control for control in meanings["controls"]}
    assert controls["Results"]["table_state"] == table_state
    assert controls["Results"]["parent_id"] == controls["Window"]["id"]
    assert controls["Reference"]["focused"] is True
    assert controls["Continue"]["parent_id"] is None
    scene = SceneObservation.model_validate_json(
        (fixture.output / "observations.jsonl").read_text(encoding="utf-8")
    )
    elements = {element.name: element for element in scene.elements if element.role != "text"}
    assert elements["Reference"].focused is True
    assert elements["Reference"].parent_ref == elements["Window"].ref
    assert elements["Continue"].parent_ref is None
    assert elements["Results"].parent_ref == elements["Window"].ref
    assert elements["Results"].enabled is (table_state != "uncertain")
    assert (elements["Results"].ref in scene.uncertain_control_refs) is (table_state == "uncertain")
    if table_state == "rows":
        assert elements["Result row"].parent_ref == elements["Results"].ref
        assert elements["Result value"].parent_ref == elements["Result row"].ref
