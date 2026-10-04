"""Synthetic visual evidence: geometry, OCR grounding, ambiguity and provenance."""

import hashlib
import io
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from PIL import Image
from sawon_interfaces import CaptureGeometry, Observation, Screenshot, WindowInfo

from brain.graph.matching import ScreenMatch
from brain.relay import HttpRelay, RelayError
from brain.visual.scene import Detection, SemanticDetection, VisualControl, bind_scene
from pipeline.schemas import OcrBlock


def wire():
    window = WindowInfo(handle=1, title="Synthetic", process="fixture.exe")
    return Observation(
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
                origin=(-400, 0),
                physical_size=(800, 400),
                dpi=144,
            ),
        ),
    )


def scene(value="AB12", confidence=0.99):
    blocks = [
        OcrBlock(id=0, text="Identifier", confidence=0.99, bbox=(10, 25, 65, 15)),
        OcrBlock(id=1, text=value, confidence=confidence, bbox=(105, 25, 45, 15)),
    ]
    detection = Detection(
        controls=[
            VisualControl(
                id=0,
                role="edit",
                bbox=(100, 20, 200, 30),
                label_ids=[0],
                text_ids=[1],
                confidence=0.99,
            )
        ]
    )
    return blocks, detection


def test_input_uses_control_rectangle_not_label_and_only_ocr_value():
    blocks, detection = scene()
    result = bind_scene(wire(), blocks, detection)
    target = result.elements[0]
    assert target.bbox == (100, 20, 200, 30)
    assert target.name == target.label_hint == "Identifier"
    assert target.value == "AB12"
    assert result.evidence_source == "ocr_visual"
    assert result.wire.elements == []
    assert result.screenshot.desktop_point((200, 35)) == (0, 70)


def test_uncertain_ocr_is_not_value_evidence():
    blocks, detection = scene(confidence=0.2)
    assert bind_scene(wire(), blocks, detection).elements[0].value is None


def test_blank_input_does_not_become_its_label():
    blocks, detection = scene()
    blank = detection.controls[0].model_copy(update={"text_ids": []})
    target = bind_scene(wire(), blocks, Detection(controls=[blank])).elements[0]
    assert target.name == "Identifier" and target.value is None


@pytest.mark.parametrize("inside", [False, True])
def test_query_uncertainty_is_scoped_without_ignoring_model_warnings(inside):
    detection = Detection(
        controls=[
            VisualControl(
                id=0, role="table", bbox=[10, 60, 380, 100], confidence=1, table_state="empty"
            ),
            VisualControl(
                id=1, role="text", bbox=[20, 80 if inside else 10, 20, 20], confidence=0.2
            ),
        ]
    )
    result = bind_scene(wire(), [], detection)
    assert result.uncertainties  # Preserve all diagnostic warnings.
    assert bool(result.uncertainties_for_region(result.elements[0].ref)) is inside
    warned = bind_scene(
        wire(), [], detection.model_copy(update={"uncertainties": ["uncertain grid"]})
    )
    assert warned.uncertainties_for_region(warned.elements[0].ref)
    assert (
        bool(
            warned.uncertainties_for_region(warned.elements[0].ref, include_general_warnings=False)
        )
        is inside
    )
    assert warned.uncertainties  # Notes remain available for diagnostics.
    old = result.model_copy(update={"global_uncertainties": None})
    assert old.uncertainties_for_region(old.elements[0].ref)
    assert old.uncertainties_for_region(old.elements[0].ref, include_general_warnings=False)


@pytest.mark.parametrize("table_state", ["empty", "uncertain", "rows"])
def test_empty_grid_uses_structured_evidence_not_header_notes(table_state):
    from brain.graph.engine import Brain, GuardStop
    from brain.graph.schemas import Decision
    from brain.graph.state import create_worker_state

    brain = Brain.__new__(Brain)
    brain._worker_state = create_worker_state()
    brain.observation = bind_scene(
        wire(),
        [],
        Detection(
            controls=[
                VisualControl(
                    id=0,
                    role="table",
                    bbox=[10, 60, 380, 100],
                    confidence=0.95,
                    table_state=table_state,
                )
            ],
            uncertainties=["Partially clipped header lacks a separate detector box"],
        ),
    )
    decision = Decision(
        operation="duplicate_clear",
        intent="verify",
        screen_kind="query",
        subgoal="Check empty results",
        reason="Read the visible result area",
        grid_ref=brain.observation.elements[0].ref,
    )
    if table_state == "empty":
        assert brain._grid(decision)
        with pytest.raises(GuardStop, match="visual_query_evidence_uncertain"):
            brain._grid(decision.model_copy(update={"operation": "complete"}))
    else:
        with pytest.raises(GuardStop, match="missing_query_grid_evidence"):
            brain._grid(decision)


@pytest.mark.parametrize(
    "update,reason",
    [
        ({"bbox": (100, 20, 400, 30)}, "outside_capture"),
        ({"text_ids": [9]}, "unknown_ocr"),
        ({"text_ids": [0]}, "value_outside"),
        ({"parent_id": 9}, "parent_bounds"),
    ],
)
def test_invalid_detector_evidence_fails_closed(update, reason):
    blocks, detection = scene()
    control = detection.controls[0].model_copy(update=update)
    with pytest.raises(ValueError, match=reason):
        bind_scene(wire(), blocks, Detection(controls=[control]))


def test_structural_parent_allows_small_independent_detector_edge_jitter():
    detection = Detection(
        controls=[
            VisualControl(
                id=0,
                role="table",
                bbox=[10, 10, 380, 100],
                confidence=0.99,
                table_state="rows",
            ),
            VisualControl(
                id=1,
                role="header",
                bbox=[293, 20, 100, 20],
                confidence=0.99,
                parent_id=0,
            ),
        ]
    )

    result = bind_scene(wire(), [], detection)
    assert result.elements[1].parent_ref == result.elements[0].ref

    invalid = detection.model_copy(
        update={
            "controls": [
                detection.controls[0],
                detection.controls[1].model_copy(update={"bbox": [210, 20, 190, 20]}),
            ]
        }
    )
    with pytest.raises(ValueError, match="parent_bounds"):
        bind_scene(wire(), [], invalid)


def test_duplicate_targets_are_both_disabled():
    blocks, detection = scene()
    controls = [detection.controls[0], detection.controls[0].model_copy(update={"id": 1})]
    result = bind_scene(wire(), blocks, Detection(controls=controls))
    assert all(not item.enabled and item.value is None for item in result.elements)


def test_contained_writable_markers_with_same_label_are_not_two_fields():
    blocks = [
        OcrBlock(id=0, text="Identifier", confidence=0.99, bbox=(10, 25, 65, 15)),
        OcrBlock(id=1, text="CURRENT", confidence=0.99, bbox=(120, 25, 70, 15)),
    ]
    controls = [
        VisualControl(
            id=20,
            role="edit",
            bbox=(100, 20, 200, 30),
            label_ids=[0],
            confidence=0.99,
        ),
        VisualControl(
            id=21,
            role="edit",
            bbox=(120, 25, 70, 15),
            label_ids=[0],
            text_ids=[1],
            confidence=0.99,
        ),
    ]

    with pytest.raises(ValueError, match="conflicting_writable_surface"):
        bind_scene(wire(), blocks, Detection(controls=controls))


def test_scene_refs_change_with_capture_but_semantics_do_not():
    blocks, detection = scene()
    first = bind_scene(wire(), blocks, detection).elements[0]
    second = bind_scene(wire(), blocks, detection).elements[0]
    assert first.ref != second.ref
    assert first.name == second.name


def test_fetch_verifies_png_hash_geometry_and_refuses_redirect_paths(tmp_path):
    data = io.BytesIO()
    Image.new("RGB", (400, 200)).save(data, format="PNG")
    raw = data.getvalue()
    observation = wire().model_copy(update={"screen_hash": hashlib.sha1(raw).hexdigest()})
    relay = HttpRelay("http://localhost:8765", "synthetic", visual=True)

    class Opener:
        calls = 0

        def open(self, request, timeout):
            self.calls += 1
            assert request.full_url == "http://localhost:8765/screenshots/test.png"
            return io.BytesIO(raw)

    relay._opener = Opener()
    assert relay.screenshot(observation, tmp_path / "good.png").read_bytes() == raw
    with pytest.raises(RelayError, match="integrity"):
        relay.screenshot(wire(), tmp_path / "bad.png")
    assert not (tmp_path / "bad.png").exists()
    wrong_size = observation.model_copy(
        update={"screenshot": observation.screenshot.model_copy(update={"width": 399})}
    )
    # Hash still matches: only the declared dimensions disagree with the valid PNG.
    with pytest.raises(RelayError, match="geometry_mismatch"):
        relay.screenshot(wrong_size, tmp_path / "wrong-size.png")
    assert not (tmp_path / "wrong-size.png").exists()
    bad = observation.model_copy(
        update={
            "screenshot": observation.screenshot.model_copy(
                update={"path": "https://other.example/private.png"}
            )
        }
    )
    with pytest.raises(RelayError, match="path"):
        relay.screenshot(bad, tmp_path / "bad.png")
    assert relay._opener.calls == 3  # Invalid paths never reach the transport.


def test_table_rows_and_cells_preserve_ocr_grounded_hierarchy():
    blocks = [OcrBlock(id=0, text="AB12", confidence=0.99, bbox=(20, 40, 40, 10))]
    detection = Detection(
        controls=[
            VisualControl(
                id=0, role="table", bbox=[10, 10, 380, 100], confidence=0.99, table_state="rows"
            ),
            VisualControl(id=1, role="row", bbox=[10, 35, 380, 25], confidence=0.99, parent_id=0),
            VisualControl(
                id=2,
                role="cell",
                bbox=[15, 36, 100, 20],
                confidence=0.99,
                parent_id=1,
                text_ids=[0],
            ),
        ]
    )
    result = bind_scene(wire(), blocks, detection)
    table, row, cell = result.elements
    assert row.parent_ref == table.ref and cell.parent_ref == row.ref
    assert cell.value == "AB12" and table.enabled
    uncertain = detection.model_copy(update={"controls": [detection.controls[0]]})
    result = bind_scene(wire(), blocks, uncertain)
    assert not result.elements[0].enabled and result.uncertainties

    # A current table-level action can use row OCR directly without requiring
    # the same row to be duplicated as a separately executable control.
    text_grounded = detection.model_copy(
        update={"controls": [detection.controls[0].model_copy(update={"text_ids": [0]})]}
    )
    result = bind_scene(wire(), blocks, text_grounded)
    assert result.elements[0].enabled
    assert result.elements[0].value == "AB12"


def test_provider_schema_is_compact_but_local_limits_remain():
    import json

    from pydantic import ValidationError

    meaning = {"id": 0, "role": "edit", "confidence": 1, "visual_description": "x" * 160}
    for model, extra in [(SemanticDetection, {}), (ScreenMatch, {"procedure_step": "fill"})]:
        schema = json.dumps(model.model_json_schema())
        assert "prefixItems" not in schema and "maxItems" not in schema
        assert "maxLength" not in schema and "bbox" not in schema
        assert len(model.model_validate({**extra, "controls": [meaning] * 500}).controls) == 500
        bounded = model.model_validate(
            {**extra, "controls": [{**meaning, "visual_description": "x" * 161}]}
        )
        assert bounded.controls[0].visual_description == "x" * 160
        for invalid in [
            {**extra, "controls": [meaning] * 501},
            {**extra, "controls": [{**meaning, "bbox": [1, 2, 3, 4]}]},
        ]:
            with pytest.raises(ValidationError):
                model.model_validate(invalid)
    with pytest.raises(ValidationError):
        Detection.model_validate(
            {"controls": [{"id": 0, "role": "edit", "bbox": [1, 2, 3], "confidence": 1}]}
        )


def test_icon_meaning_is_visible_to_planner_but_never_value_evidence():
    from brain.observe import summarize

    detection = Detection(
        controls=[
            VisualControl(
                id=0,
                role="icon",
                bbox=[10, 10, 20, 20],
                confidence=0.95,
                visual_description="magnifying glass",
            )
        ]
    )
    result = bind_scene(wire(), [], detection)
    assert result.elements[0].value is None
    assert "magnifying glass" in summarize(result, "label_grouped")
