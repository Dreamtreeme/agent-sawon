"""Adapters are checked against documented Paddle result shapes, without OCR downloads."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from pipeline.extract import PipelineCallBudget
from pipeline.ocr import PaddleOcrEngine
from pipeline.ocr_worker import recognize
from pipeline.render import PipelineDependencyError, render_document
from pipeline.schemas import DocumentInput, OcrBlock, PageExtraction, PipelineSettings, RenderedPage


@pytest.fixture
def page(tmp_path):
    return RenderedPage(
        document="originals/hbl.png",
        kind="hbl",
        page=1,
        image_path=tmp_path / "hbl.png",
        width=100,
        height=100,
    )


def test_paddle_v3_result_boxes_converted_to_xywh():
    fake = SimpleNamespace(
        predict=lambda _: [
            SimpleNamespace(
                json={
                    "res": {
                        "rec_texts": ["Cargo", "12 CT"],
                        "rec_scores": [0.99, 0.98],
                        "rec_boxes": [[10, 20, 70, 35], [10, 40, 90, 55]],
                    }
                }
            )
        ]
    )
    result = recognize(fake, "not-read.png")
    assert result[0]["bbox"] == [10, 20, 60, 15]
    assert result[1]["id"] == 1 and result[1]["confidence"] == 0.98


def test_compact_provider_schema_retains_local_resource_bounds():
    from pydantic import ValidationError

    schema = json.dumps(PageExtraction.model_json_schema())
    assert "maxItems" not in schema and "maxLength" not in schema
    field = {
        "name": "hbl_no",
        "value_text": "Sample",
        "evidence_block_ids": [0],
        "model_confidence": 0.99,
    }
    with pytest.raises(ValidationError):
        PageExtraction.model_validate({"fields": [field] * 51})
    with pytest.raises(ValidationError):
        PageExtraction.model_validate({"fields": [{**field, "value_text": "x" * 2001}]})


def test_paddle_malformed_parallel_arrays_are_rejected():
    fake = SimpleNamespace(
        predict=lambda _: [
            SimpleNamespace(
                json={
                    "res": {
                        "rec_texts": ["Cargo"],
                        "rec_scores": [],
                        "rec_boxes": [],
                    }
                }
            )
        ]
    )
    with pytest.raises(ValueError, match="shape_mismatch"):
        recognize(fake, "not-read.png")


def test_missing_paddle_is_actionable_not_fake_ocr(monkeypatch, page):
    def missing(*_):
        raise ModuleNotFoundError("paddleocr")

    monkeypatch.setattr("pipeline.ocr.create_engine", missing)
    with pytest.raises(PipelineDependencyError, match="No OCR fallback"):
        PaddleOcrEngine(PipelineSettings(ocr_execution="in_process")).read(page)


def test_ocr_outside_page_rejected(monkeypatch, page):
    monkeypatch.setattr("pipeline.ocr.create_engine", lambda *_: object())
    monkeypatch.setattr(
        "pipeline.ocr.recognize",
        lambda *_: [{"id": 0, "text": "Too wide", "confidence": 0.99, "bbox": [0, 0, 200, 40]}],
    )
    with pytest.raises(ValueError, match="outside_page"):
        PaddleOcrEngine(PipelineSettings(ocr_execution="in_process")).read(page)


def test_image_render_preserves_real_pixels_and_page_coordinates(tmp_path):
    original = tmp_path / "source.png"
    Image.new("RGB", (1000, 1000), "blue").save(original)
    pages = render_document(
        DocumentInput(kind="hbl", path=original),
        tmp_path / "pages",
        PipelineSettings(max_pixels=250_000),
    )
    assert len(pages) == 1 and pages[0].width == pages[0].height == 500
    with Image.open(pages[0].image_path) as image:
        assert image.getpixel((10, 10)) == (0, 0, 255)


def test_multipage_image_limit_blocks(tmp_path):
    original = tmp_path / "source.tiff"
    page = Image.new("RGB", (100, 100), "white")
    page.save(original, save_all=True, append_images=[page])
    with pytest.raises(ValueError, match="page_limit"):
        render_document(
            DocumentInput(kind="hbl", path=original),
            tmp_path / "pages",
            PipelineSettings(max_pages=1),
        )


def test_budget_includes_image_and_output_reservation(page):
    budget = PipelineCallBudget.from_config(Path("config/models.lite.toml"))
    page = page.model_copy(update={"width": 1200, "height": 1600})
    assert budget("test", PageExtraction, page) > budget.text("test", PageExtraction)
    replay = PipelineCallBudget(text=None)
    assert replay("test", PageExtraction, page) == Decimal(0)


def test_external_worker_uses_argument_list_hidden_no_shell(monkeypatch, page):
    observed = []

    def run(arguments, **kwargs):
        observed.append((arguments, kwargs))
        request = json.loads(Path(arguments[-2]).read_text(encoding="utf-8"))
        assert request["image"] == str(page.image_path.resolve())
        Path(arguments[-1]).write_text(
            json.dumps(
                {"blocks": [{"id": 0, "text": "12 CT", "confidence": 0.9, "bbox": [0, 0, 30, 10]}]}
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr("pipeline.ocr.run_worker", run)
    blocks = PaddleOcrEngine(PipelineSettings(ocr_python="C:/OCR env/python.exe")).read(page)
    assert blocks == [OcrBlock(id=0, text="12 CT", confidence=0.9, bbox=(0, 0, 30, 10))]
    assert observed[0][0][0] == "C:/OCR env/python.exe"
    assert "shell" not in observed[0][1]
    assert observed[0][1]["timeout"] == 300
    assert observed[0][1]["log_path"].name == "worker.log"
    assert not list(page.image_path.parent.glob(".ocr-*"))
