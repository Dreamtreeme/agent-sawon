"""Lazy PaddleOCR adapter. Missing OCR fails explicitly; it has no fake fallback."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Protocol

from .ocr_worker import create_engine, recognize
from .persistent_worker import PersistentWorker, WorkerError
from .render import PipelineDependencyError
from .schemas import OcrBlock, PipelineSettings, RenderedPage


class OcrEngine(Protocol):
    def read(self, page: RenderedPage) -> list[OcrBlock]: ...


class PaddleOcrEngine:
    def __init__(self, settings: PipelineSettings):
        self.settings = settings
        self._engine = None
        self._worker = None
        self.last_metrics = {}

    def read(self, page: RenderedPage) -> list[OcrBlock]:
        if self.settings.ocr_execution == "subprocess" or self.settings.ocr_python:
            blocks = self._external(page)
        else:
            try:
                if self._engine is None:
                    self._engine = create_engine(
                        self.settings.ocr_language,
                        self.settings.ocr_device,
                        self.settings.ocr_enable_mkldnn,
                        self.settings.ocr_cpu_threads,
                        self.settings.ocr_version,
                        self.settings.ocr_detection_model,
                        self.settings.ocr_recognition_model,
                    )
                blocks = recognize(self._engine, str(page.image_path))
            except (ImportError, ModuleNotFoundError) as exc:
                raise PipelineDependencyError(
                    "PaddleOCR requires paddleocr and paddlepaddle; install core[ocr] or "
                    "configure ocr_python with an OCR environment. No OCR fallback was used."
                ) from exc
        result = [OcrBlock.model_validate(block) for block in blocks]
        if len(result) > self.settings.max_ocr_blocks:
            raise ValueError("ocr_block_limit")
        if sum(len(block.text) for block in result) > self.settings.max_ocr_characters:
            raise ValueError("ocr_text_limit")
        if len({block.id for block in result}) != len(result):
            raise ValueError("duplicate_ocr_block_id")
        for block in result:
            x, y, width, height = block.bbox
            if x + width > page.width or y + height > page.height:
                raise ValueError("ocr_bbox_outside_page")
        return result

    def close(self) -> None:
        if self._worker is not None:
            self._worker.close()
        self._engine = None

    def _external(self, page: RenderedPage) -> list[dict]:
        if self._worker is None:
            self._worker = PersistentWorker(
                [
                    self.settings.ocr_python or sys.executable,
                    str(Path(__file__).with_name("ocr_worker.py")),
                    "--serve",
                ],
                directory=page.image_path.parent,
            )
        try:
            result = self._worker.request(
                {
                    "image": str(page.image_path.resolve()),
                    "language": self.settings.ocr_language,
                    "device": self.settings.ocr_device,
                    "enable_mkldnn": self.settings.ocr_enable_mkldnn,
                    "cpu_threads": self.settings.ocr_cpu_threads,
                    "ocr_version": self.settings.ocr_version,
                    "detection_model": self.settings.ocr_detection_model,
                    "recognition_model": self.settings.ocr_recognition_model,
                },
                timeout=self.settings.ocr_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise PipelineDependencyError("PaddleOCR worker exceeded its time limit") from exc
        except FileNotFoundError as exc:
            raise PipelineDependencyError("Configured ocr_python executable is missing") from exc
        except WorkerError as exc:
            raise PipelineDependencyError(
                "PaddleOCR worker failed; verify its environment/models"
            ) from exc
        self.last_metrics = self._worker.last_metrics
        return result["blocks"]
