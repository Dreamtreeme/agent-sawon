"""Coordinate invention regression and local detector -> semantic -> scene boundary."""

import json

import pytest
from PIL import Image
from pydantic import ValidationError
from test_visual_scene import wire

from brain.runtime.storage import RunLog
from brain.visual.detector import DetectorSettings, OmniParserDetector
from brain.visual.detector_worker import original_box, restore_region_box
from brain.visual.markers import Marker, MergeSettings, build_markers, ground_meanings
from brain.visual.perception import VisualGroundingError, VisualPerception
from brain.visual.refinement import refinement_regions
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


@pytest.mark.parametrize("text_ids", [[8], []])
def test_ocr_caption_can_be_a_tab_target_without_detector_extent(text_ids):
    blocks, _, markers, _ = fixture()
    label = next(m for m in markers if m.source == "ocr")
    meaning = SemanticDetection.model_validate(
        {
            "screen_kind": "editor",
            "controls": [
                {"id": label.id, "role": "tabitem", "text_ids": text_ids, "confidence": 0.95}
            ],
        }
    )
    scene = bind_scene(wire(), blocks, ground_meanings(meaning, markers, blocks))
    tab = next(e for e in scene.elements if e.role == "tabitem")
    assert tab.enabled and tab.bbox == blocks[0].bbox
    assert tab.name == "Reference"


def test_ocr_caption_cannot_replace_its_literal_text():
    blocks, _, markers, _ = fixture()
    label = next(m for m in markers if m.source == "ocr")
    meaning = SemanticDetection.model_validate(
        {"controls": [{"id": label.id, "role": "tabitem", "text_ids": [999], "confidence": 1}]}
    )
    with pytest.raises(ValueError, match="ocr_marker_text_mismatch"):
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


@pytest.mark.parametrize("outcome", ["valid", "corrected", "invalid", "provider_error"])
def test_complete_perception_uses_marked_image_and_preserves_raw_capture(tmp_path, outcome):
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

    def interpret(request):
        prompt, page, payload = request.prompt(), request.page, request.screen
        assert page.image_path.name == "markers.png"
        assert "markers" in prompt
        assert all("confidence" not in marker for marker in payload["markers"])
        assert all("candidate_confidence" in marker for marker in payload["markers"])
        assert {marker["source"] for marker in payload["markers"]} == {"omniparser", "ocr"}
        text = next(m for m in payload["markers"] if m["source"] == "ocr")
        assert text["contained_ocr_ids"] == [8]
        with Image.open(page.image_path) as picture:
            assert picture.getpixel((10, 20)) == (255, 0, 0)
        assert "NOT markers[].id" in prompt
        calls.append(page)
        if outcome == "provider_error":
            raise ValueError("provider_failure_with_private_details")
        if outcome == "invalid" or (outcome == "corrected" and len(calls) == 1):
            return meaning.model_copy(
                update={"controls": [meaning.controls[0].model_copy(update={"text_ids": [8]})]}
            )
        if len(calls) == 2:
            assert '"error": "value_outside_local_marker"' in prompt
        return meaning

    log = RunLog(tmp_path, case_id="markers", agent_id="test")
    perception = VisualPerception(Relay(), Ocr(), tmp_path, log, interpret, detector=Detector())
    if outcome in {"invalid", "provider_error"}:
        with pytest.raises(VisualGroundingError if outcome == "invalid" else ValueError):
            perception.observe(wire())
        assert len(calls) == (2 if outcome == "invalid" else 1)
        assert not (calls[0].image_path.parent / "perception.json").exists()
        return
    scene = perception.observe(wire())
    assert len(calls) == (2 if outcome == "corrected" else 1)
    assert any(e.role == "edit" for e in scene.elements)
    if outcome == "corrected":
        rejected = [
            e for e in log.read_all() if e.payload.get("stage") == "visual_semantic_rejection"
        ]
        assert len(rejected) == 1 and rejected[0].payload["retry"]
        assert (calls[0].image_path.parent / "meanings-attempt-0.json").is_file()
    folder = calls[0].image_path.parent
    with Image.open(folder / "capture.png") as image:
        assert image.getpixel((100, 15)) == (255, 255, 255)
    assert (folder / "markers.json").is_file() and (folder / "meanings.json").is_file()


def test_settings_resolve_local_weights_relative_to_config(tmp_path):
    from brain.settings import BrainSettings

    config = tmp_path / "brain.toml"
    config.write_text('[visual_detector]\nweights="weights/model.pt"', encoding="utf-8")
    assert BrainSettings.load(config).visual_detector.weights == tmp_path / "weights/model.pt"


def test_ocr_tolerance_removes_tall_glyphs_but_preserves_larger_button():
    block = OcrBlock(id=0, text="Example", confidence=0.99, bbox=(20, 30, 100, 22))
    glyph = Marker(id=0, bbox=(40, 27, 24, 29), source="omniparser", confidence=0.7)
    button = Marker(id=1, bbox=(10, 20, 125, 45), source="omniparser", confidence=0.3)
    markers = build_markers([block], [glyph, button], 300, 200)
    assert [m.bbox for m in markers if m.source == "omniparser"] == [button.bbox]
    # Weak OCR must not erase a legitimate detector candidate.
    markers = build_markers(
        [block.model_copy(update={"confidence": 0.2})], [glyph, button], 300, 200
    )
    assert len([m for m in markers if m.source == "omniparser"]) == 2
    markers = build_markers([block], [glyph], 300, 200, settings=MergeSettings(max_text_padding=0))
    assert any(m.source == "omniparser" for m in markers)


def test_concentric_icon_dedup_does_not_remove_small_table_child():
    boxes = [
        Marker(id=i, bbox=b, source="omniparser", confidence=c)
        for i, (b, c) in enumerate(
            [
                ((10, 10, 30, 40), 0.2),
                ((13, 18, 24, 24), 0.4),
                ((100, 10, 180, 120), 0.8),
                ((120, 30, 60, 20), 0.7),
            ]
        )
    ]
    kept = [m.bbox for m in build_markers([], boxes, 300, 200)]
    assert (10, 10, 30, 40) not in kept
    assert (13, 18, 24, 24) in kept and (120, 30, 60, 20) in kept


def test_unmatched_label_creates_only_bounded_search_region():
    blocks = [
        OcrBlock(id=i, text="Field", confidence=0.99, bbox=(10, y, 50, 20))
        for i, y in enumerate((40, 160, 280))
    ]
    boxes = [Marker(id=0, bbox=(100, 35, 180, 30), source="omniparser", confidence=0.4)]
    regions = refinement_regions(blocks, boxes, 400, 400, limit=1)
    assert regions == [(0, 100, 400, 140)]
    # Crops are search areas only, never appended as click candidates.
    assert len(boxes) == 1


def test_zoom_restores_crop_origin_and_rejects_cut_off_control():
    assert restore_region_box([40, 40, 200, 100], (10, 100, 200, 100), (400, 200), (400, 400)) == [
        30,
        120,
        80,
        30,
    ]
    assert (
        restore_region_box([40, 0, 200, 100], (10, 100, 200, 100), (400, 200), (400, 400)) is None
    )
    assert (
        restore_region_box([40, 40, 400, 100], (10, 100, 200, 100), (400, 200), (400, 400)) is None
    )


def test_refinement_budget_stops_before_a_model_call(tmp_path):
    log = RunLog(tmp_path, case_id="bounded", agent_id="test")
    detector = OmniParserDetector(DetectorSettings(refinement_max_regions=1), log)
    with pytest.raises(ValueError, match="refinement_region_limit"):
        detector.refine(None, [(0, 0, 10, 10), (0, 10, 10, 10)])
    assert not log.read_all()


@pytest.mark.parametrize("missing,on_rematch", [(False, False), (True, False), (True, True)])
def test_perception_refines_only_after_basic_detection_reports_failure(
    tmp_path, missing, on_rematch, monkeypatch
):
    blocks, boxes, _, meaning = fixture()
    blocks.append(OcrBlock(id=9, text="Second", confidence=0.99, bbox=(10, 120, 60, 15)))
    log = RunLog(tmp_path, case_id="refinement", agent_id="test")
    calls = []
    clock = [0.0]
    monkeypatch.setattr("brain.visual.perception.perf_counter", lambda: clock[0])

    class Detector(OmniParserDetector):
        def read(self, page):
            calls.append("full")
            clock[0] += 2
            return boxes

        def refine(self, page, regions):
            assert 1 <= len(regions) <= self.settings.refinement_max_regions
            calls.append("refine")
            clock[0] += 10
            return [Marker(id=0, bbox=(100, 115, 150, 30), source="omniparser", confidence=0.5)]

    class Relay:
        def screenshot(self, observation, path):
            path.parent.mkdir(parents=True)
            Image.new("RGB", (400, 200), "white").save(path)
            return path

    class Ocr:
        def read(self, page):
            return blocks

    def interpret(request):
        prompt = request.prompt()
        calls.append("meaning")
        clock[0] += 1
        assert '"refinement' not in prompt  # Search regions are not selectable markers.
        return meaning.model_copy(
            update={"needs_refinement": missing and calls.count("meaning") == 1 + on_rematch}
        )

    perception = VisualPerception(
        Relay(), Ocr(), tmp_path, log, interpret, detector=Detector(DetectorSettings(), log)
    )
    current = wire()
    perception.observe(current)
    if on_rematch:
        perception.rematch(current)
    assert calls == ["full", "meaning"] + (["meaning"] if on_rematch else []) + (
        ["refine", "meaning"] if missing else []
    )
    event = [e for e in log.read_all() if e.payload.get("stage") == "visual_perception"][-1]
    assert event.payload["detection_ms"] == (2000 if missing else 1000)
    assert event.payload["local_detector_ms"] == (
        (10000 if missing else 0) + (0 if on_rematch else 2000)
    )


def test_same_screen_rematch_corrects_and_updates_cached_meaning_without_new_ocr(tmp_path):
    blocks, boxes, _, meaning = fixture()
    calls = {"capture": 0, "ocr": 0, "detector": 0}
    requests = []

    class Relay:
        def screenshot(self, observation, path):
            calls["capture"] += 1
            path.parent.mkdir(parents=True)
            Image.new("RGB", (400, 200), "white").save(path)
            return path

    class Ocr:
        def read(self, page):
            calls["ocr"] += 1
            return blocks

    class Detector:
        def read(self, page):
            calls["detector"] += 1
            return boxes

    def interpret(request):
        requests.append(request)
        update = {"text_ids": [8]} if len(requests) == 2 else {"role": "combobox"}
        return (
            meaning
            if len(requests) == 1
            else meaning.model_copy(
                update={"controls": [meaning.controls[0].model_copy(update=update)]}
            )
        )

    perception = VisualPerception(
        Relay(),
        Ocr(),
        tmp_path,
        RunLog(tmp_path, case_id="rematch", agent_id="test"),
        interpret,
        detector=Detector(),
        reuse_identical=True,
    )
    assert perception.observe(wire()).elements[0].role == "edit"
    current = wire()
    assert perception.observe(current).elements[0].role == "edit"
    before_rematch = calls.copy()
    corrected = perception.rematch(current)
    assert calls == before_rematch  # Rematching must not capture or run local models.
    assert corrected.obs_id == current.obs_id and corrected.elements[0].role == "combobox"
    assert requests[1].correction is None
    assert requests[2].correction["error"] == "value_outside_local_marker"
    assert perception.observe(wire()).elements[0].role == "combobox"
    assert calls == {"capture": 3, "ocr": 1, "detector": 1}
    assert len(requests) == 3


def test_input_value_comes_from_contained_ocr_when_model_omits_text_ids():
    blocks, _, markers, meaning = fixture()
    blocks.append(OcrBlock(id=9, text="CT", bbox=(110, 20, 20, 15), confidence=0.99))
    scene = bind_scene(wire(), blocks, ground_meanings(meaning, markers, blocks))
    field = next(e for e in scene.elements if e.role == "edit")
    assert field.value == "CT"
    assert field.name == "Reference"


@pytest.mark.parametrize("clipped", [False, True])
@pytest.mark.parametrize("explicit_cells", [True, False])
def test_ocr_rows_feed_existing_final_review_without_inventing_values(clipped, explicit_cells):
    from test_visual_scene import wire

    from brain.graph.review import review_final
    from brain.observe.values import field_matches
    from brain.visual.scene import SemanticDetection, bind_scene

    blocks = [
        OcrBlock(id=0, text="Identifier", bbox=(10, 10, 70, 15), confidence=0.99),
        OcrBlock(id=1, text="Cargo", bbox=(130, 10, 50, 15), confidence=0.99),
        OcrBlock(id=2, text="ID-A", bbox=(10, 40, 50, 15), confidence=0.99),
        OcrBlock(
            id=3, text="par..." if clipped else "parts", bbox=(130, 40, 50, 15), confidence=0.99
        ),
    ]
    markers = build_markers(
        blocks,
        [Marker(id=0, bbox=(1, 1, 350, 150), source="pixel_rectangle", confidence=1)],
        400,
        200,
    )
    table = next(m for m in markers if m.source == "pixel_rectangle")
    row = next(m for m in markers if m.source == "ocr_group" and m.text_ids == (2, 3))
    cells = [m for m in markers if m.source == "ocr" and m.text_ids[0] in {2, 3}]
    meanings = SemanticDetection(
        screen_kind="query",
        controls=[
            {"id": table.id, "role": "table", "confidence": 0.99, "table_state": "rows"},
            {"id": row.id, "role": "row", "parent_id": table.id, "confidence": 0.99},
            *[
                {
                    "id": m.id,
                    "role": "cell",
                    "parent_id": row.id,
                    "confidence": 0.99,
                    "text_ids": list(m.text_ids),
                }
                for m in cells
                if explicit_cells
            ],
        ],
    )
    scene = bind_scene(wire(), blocks, ground_meanings(meanings, markers, blocks))
    review = review_final(scene, {"hbl_no": "ID-A", "description": "parts"}, field_matches)
    assert review.status == ("needs_more" if clipped else "ready")
    assert review.missing_fields == (("description",) if clipped else ())
    invalid = meanings.model_copy(
        update={
            "controls": [
                meanings.controls[1].model_copy(update={"role": "edit", "parent_id": None})
            ]
        }
    )
    with pytest.raises(ValueError, match="ocr_group_is_not_input"):
        ground_meanings(invalid, markers, blocks)


def test_row_groups_require_repeated_columns_inside_measured_container():
    from brain.visual.row_candidates import row_candidates

    blocks = [
        OcrBlock(id=0, text="one", bbox=(10, 10, 30, 10), confidence=0.99),
        OcrBlock(id=1, text="two", bbox=(60, 10, 30, 10), confidence=0.99),
    ]
    assert row_candidates(blocks, [(0, 0, 100, 100)]) == []
    assert row_candidates(blocks, []) == []


def test_overlapping_ocr_edges_do_not_drop_a_data_row():
    from brain.visual.row_candidates import row_candidates

    blocks = [
        OcrBlock(id=0, text="A", bbox=(10, 10, 30, 10), confidence=0.99),
        OcrBlock(id=1, text="B", bbox=(60, 10, 30, 10), confidence=0.99),
        OcrBlock(id=2, text="data-a", bbox=(10, 30, 55, 10), confidence=0.99),
        OcrBlock(id=3, text="data-b", bbox=(60, 30, 30, 10), confidence=0.99),
    ]
    assert [ids for _, ids in row_candidates(blocks, [(0, 0, 100, 100)])] == [(0, 1), (2, 3)]
