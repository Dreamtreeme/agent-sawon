"""Pixel preprocessing must preserve source provenance and document discrepancies."""

from __future__ import annotations

import hashlib
import json

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError
from test_pipeline import FakeMeasuredVision, FakeOcr

from pipeline.checks import equivalent
from pipeline.normalize import normalize_value
from pipeline.ocr_input import prepare_ocr_input, read_ocr_views
from pipeline.runner import prepare_case
from pipeline.schemas import DocumentInput, ExtractedPage, OcrBlock, PipelineSettings, RenderedPage
from sawon_runtime.storage import RunLog


def page_fixture(tmp_path):
    path = tmp_path / "pages" / "fax.png"
    path.parent.mkdir()
    image = Image.new("RGB", (400, 260), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 50, 50), fill="black")
    image.putpixel((100, 100), (0, 0, 0))
    image.save(path)
    return RenderedPage(
        document="originals/fax.png",
        kind="hbl",
        page=1,
        image_path=path,
        width=400,
        height=260,
    )


def test_filter_removes_isolated_noise_and_preserves_original_and_coordinates(tmp_path):
    page = page_fixture(tmp_path)
    original = page.image_path.read_bytes()
    processed, metadata = prepare_ocr_input(
        page, PipelineSettings(ocr_median_kernel=3), case_dir=tmp_path
    )
    assert page.image_path.read_bytes() == original
    assert processed.image_path != page.image_path
    assert (processed.width, processed.height) == (page.width, page.height)
    assert processed.document == page.document and processed.page == page.page
    assert metadata.coordinate_mapping == "identity" and metadata.median_kernel == 3
    assert metadata.sha256 == hashlib.sha256(processed.image_path.read_bytes()).hexdigest()
    assert tmp_path / metadata.image == processed.image_path
    with Image.open(processed.image_path) as image:
        assert image.getpixel((100, 100)) == (255, 255, 255)
        assert image.getpixel((35, 35)) == (0, 0, 0)
    with pytest.raises(FileExistsError):
        prepare_ocr_input(page, PipelineSettings(ocr_median_kernel=3), case_dir=tmp_path)


def test_default_is_no_pixel_change(tmp_path):
    page = page_fixture(tmp_path)
    processed, metadata = prepare_ocr_input(page, PipelineSettings(), case_dir=tmp_path)
    assert processed == page and metadata.median_kernel == 0
    assert not (page.image_path.parent / "ocr-input").exists()


def test_dimension_mismatch_rejected_before_ocr(tmp_path):
    page = page_fixture(tmp_path).model_copy(update={"width": 401})
    with pytest.raises(ValueError, match="dimensions_mismatch"):
        prepare_ocr_input(page, PipelineSettings(ocr_median_kernel=3), case_dir=tmp_path)


def test_dual_views_have_unique_ids_and_do_not_claim_an_extra_document(tmp_path):
    page = page_fixture(tmp_path)
    settings = PipelineSettings(ocr_median_kernel=3, ocr_include_original=True)
    filtered, metadata = prepare_ocr_input(page, settings, case_dir=tmp_path)

    class Readings:
        def __init__(self, supplied_blocks=None):
            self.supplied_blocks = supplied_blocks
            self.calls = []

        def read(self, supplied):
            self.calls.append(supplied.image_path)
            if self.supplied_blocks is not None:
                return self.supplied_blocks
            return [
                OcrBlock(
                    id=0,
                    text="O001" if supplied.image_path == page.image_path else "0001",
                    confidence=0.95,
                    bbox=(20, 20, 30, 10),
                )
            ]

    blocks = read_ocr_views(Readings(), page, filtered, settings)
    assert [block.id for block in blocks] == [0, 1]
    assert [block.input_variant for block in blocks] == ["original", "median"]
    assert [block.text for block in blocks] == ["O001", "0001"]
    assert metadata.original_image == page.image_path.relative_to(tmp_path).as_posix()
    assert metadata.original_sha256 == hashlib.sha256(page.image_path.read_bytes()).hexdigest()
    assert filtered.document == page.document and filtered.page == page.page
    block = blocks[0]
    for supplied, limits, error, calls in [
        (None, {"max_ocr_blocks": 1}, "ocr_block_limit", 2),
        ([block, block], {}, "duplicate_ocr_block_id", 1),
        ([block.model_copy(update={"bbox": (390, 20, 30, 10)})], {}, "ocr_bbox_outside_page", 1),
        (
            [block.model_copy(update={"text": "x" * 51})],
            {"max_ocr_characters": 100},
            "ocr_text_limit",
            2,
        ),
    ]:
        readings = Readings(supplied)
        with pytest.raises(ValueError, match=error):
            read_ocr_views(readings, page, filtered, settings.model_copy(update=limits))
        # Invalid per-view IDs/bounds fail before renumbering or reading the next
        # view; individually valid resource sizes can still exceed the combined cap.
        assert readings.calls == [page.image_path, filtered.image_path][:calls]


def test_duplicate_views_without_filter_are_invalid():
    with pytest.raises(ValidationError, match="comparison requires"):
        PipelineSettings(ocr_include_original=True)


@pytest.mark.parametrize(
    ("name", "first", "second", "matches"),
    [
        ("description", "Battery; parts", "Battery, parts", False),
        ("description", "  Battery   PARTS ", "battery parts", True),
        ("consignee", "Company-A", "Company A", False),
        ("hbl_no", "AB-01", "AB 01", False),
        ("hbl_no", "AB 01", "AB01", False),
        ("hbl_no", "AB  01", "AB 01", False),
        ("hbl_no", " ａｂ-01 ", "AB-01", True),
    ],
)
def test_document_comparison_retains_punctuation_and_identifier_spaces(
    name, first, second, matches
):
    assert equivalent(name, first, second) is matches
    if name == "hbl_no":
        assert (
            equivalent(name, normalize_value(name, first), normalize_value(name, second)) is matches
        )


@pytest.mark.parametrize("include_original", [False, True])
def test_pipeline_uses_original_for_vision_and_retains_conflict(tmp_path, include_original):
    page = page_fixture(tmp_path)
    documents = [
        DocumentInput(kind=kind, path=page.image_path)
        for kind in ("hbl", "invoice", "packing_list")
    ]
    seen_ocr = []
    seen_vision = []

    class TrackingOcr(FakeOcr):
        def read(self, page):
            seen_ocr.append(page.image_path)
            return super().read(page)

    class TrackingVision(FakeMeasuredVision):
        def _generate(self, prompt, schema, *, images=None):
            seen_vision.extend(images)
            return super()._generate(prompt, schema, images=images)

    case = tmp_path / "case"
    result = prepare_case(
        case,
        case_id="fax-conflict",
        documents=documents,
        settings=PipelineSettings(ocr_median_kernel=3, ocr_include_original=include_original),
        ocr=TrackingOcr(overrides={"packing_list": {"packages": ("13", "CT")}}),
        model_factory=TrackingVision,
    )
    assert not result.ready
    assert any(check.code == "package_count_difference" for check in result.checks.checks)
    assert len(seen_vision) == 3
    assert len(seen_ocr) == (6 if include_original else 3)
    assert sum(path.parent.name == "ocr-input" for path in seen_ocr) == 3
    assert all(path.parent.name == "pages" for path in seen_vision)
    extracted = json.loads((case / "extracted.json").read_text())
    for raw in extracted["pages"]:
        item = ExtractedPage.model_validate(raw)
        assert item.schema_version == "0.2"
        assert item.ocr_input.median_kernel == 3
        assert item.ocr_input.image != item.image
        assert {block.input_variant for block in item.blocks} == (
            {"original", "median"} if include_original else {"median"}
        )
        assert len({block.id for block in item.blocks}) == len(item.blocks)
        assert item.fields[0].source.bbox == (5, 5, 350, 18)
    assert len(list((case / "ocr").glob("*.json"))) == 3
    events = RunLog(case, case_id="fax-conflict", agent_id="test").read_all()
    assert sum(event.kind == "model_call" for event in events) == 3
