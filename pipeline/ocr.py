"""Lazy PaddleOCR adapter. Missing OCR fails explicitly; it has no fake fallback."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol

from .ocr_worker import create_engine, recognize
from .render import PipelineDependencyError
from .schemas import OcrBlock, PipelineSettings, RenderedPage
from .worker_process import run_worker


class OcrEngine(Protocol):
    def read(self, page: RenderedPage) -> list[OcrBlock]: ...


class PaddleOcrEngine:
    def __init__(self, settings: PipelineSettings):
        self.settings = settings
        self._engine = None

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

    def _external(self, page: RenderedPage) -> list[dict]:
        # One fixed script, list arguments, no shell/string interpolation.
        with tempfile.TemporaryDirectory(prefix=".ocr-", dir=page.image_path.parent) as folder:
            request, output = Path(folder) / "request.json", Path(folder) / "output.json"
            request.write_text(
                json.dumps(
                    {
                        "image": str(page.image_path.resolve()),
                        "language": self.settings.ocr_language,
                        "device": self.settings.ocr_device,
                        "enable_mkldnn": self.settings.ocr_enable_mkldnn,
                        "cpu_threads": self.settings.ocr_cpu_threads,
                        "ocr_version": self.settings.ocr_version,
                        "detection_model": self.settings.ocr_detection_model,
                        "recognition_model": self.settings.ocr_recognition_model,
                    }
                ),
                encoding="utf-8",
            )
            try:
                returncode = run_worker(
                    [
                        self.settings.ocr_python or sys.executable,
                        str(Path(__file__).with_name("ocr_worker.py")),
                        str(request),
                        str(output),
                    ],
                    log_path=Path(folder) / "worker.log",
                    timeout=self.settings.ocr_timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise PipelineDependencyError("PaddleOCR worker exceeded its time limit") from exc
            except FileNotFoundError as exc:
                raise PipelineDependencyError(
                    "Configured ocr_python executable is missing"
                ) from exc
            if returncode or not output.is_file():
                raise PipelineDependencyError(
                    "PaddleOCR worker failed; verify its Python, paddleocr/paddlepaddle and models"
                )
            return json.loads(output.read_text(encoding="utf-8"))["blocks"]
