"""Bounded local OmniParser inference; no network or LLM coordinate fallback."""

from __future__ import annotations

import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from models.base import EventSink, MeasuredModelClient, ProviderReply, TokenUsage
from pipeline.schemas import RenderedPage

from .markers import Marker


class DetectorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    weights: Path = Path("models/omniparser/icon_detect/model.pt")
    python: str | None = None
    confidence: float = Field(default=0.15, gt=0, le=1, allow_inf_nan=False)
    max_dimension: int = Field(default=1920, ge=640, le=4096)
    timeout_seconds: float = Field(default=120, gt=0, le=300, allow_inf_nan=False)
    refinement_enabled: bool = True
    refinement_max_regions: int = Field(default=3, ge=1, le=6)
    refinement_scale: float = Field(default=2.0, ge=1, le=3, allow_inf_nan=False)
    pixel_rectangles_enabled: bool = False
    pixel_rectangles_limit: int = Field(default=80, ge=1, le=100)


class ControlDetector(Protocol):
    def read(self, page: RenderedPage) -> list[Marker]: ...


class MarkerBatch(BaseModel):
    markers: list[Marker] = Field(max_length=500)


class OmniParserDetector(MeasuredModelClient):
    def __init__(self, settings: DetectorSettings, run_log: EventSink):
        super().__init__(provider="local", model="omniparser-icon-detect", run_log=run_log)
        self.settings = settings

    def read(self, page: RenderedPage) -> list[Marker]:
        return self._read(page, [])

    def refine(self, page: RenderedPage, regions: list[tuple[int, int, int, int]]) -> list[Marker]:
        if not self.settings.refinement_enabled or not regions:
            return []
        if len(regions) > self.settings.refinement_max_regions:
            raise ValueError("refinement_region_limit")
        return self._read(page, regions)

    def _read(self, page: RenderedPage, regions: list[tuple[int, int, int, int]]) -> list[Marker]:
        return self.complete_json(
            json.dumps({"settings": self.settings.model_dump(mode="json"), "regions": regions}),
            MarkerBatch,
            images=[page.image_path],
        ).response.markers

    def _cost(self, usage: TokenUsage) -> Decimal:
        return Decimal(0)

    def _generate(
        self, prompt: str, schema: type[BaseModel], *, images: list[Path] | None
    ) -> ProviderReply:
        from pipeline.worker_process import run_worker

        if not images or len(images) != 1:
            raise ValueError("detector_requires_one_image")
        if not self.settings.weights.is_file():
            raise ValueError("omniparser_weights_missing")
        regions = json.loads(prompt)["regions"]
        with tempfile.TemporaryDirectory(prefix=".detect-", dir=images[0].parent) as folder:
            request, output = Path(folder) / "request.json", Path(folder) / "output.json"
            request.write_text(
                json.dumps(
                    {
                        "image": str(images[0].resolve()),
                        "weights": str(self.settings.weights.resolve()),
                        "confidence": self.settings.confidence,
                        "max_dimension": self.settings.max_dimension,
                        "regions": regions,
                        "refinement_scale": self.settings.refinement_scale,
                    }
                ),
                encoding="utf-8",
            )
            code = run_worker(
                [
                    self.settings.python or sys.executable,
                    str(Path(__file__).with_name("detector_worker.py")),
                    str(request),
                    str(output),
                ],
                log_path=Path(folder) / "worker.log",
                timeout=self.settings.timeout_seconds,
            )
            if code or not output.is_file():
                raise ValueError("omniparser_worker_failed")
            markers = [Marker.model_validate(b) for b in json.loads(output.read_text("utf-8"))]
            return ProviderReply(
                text=MarkerBatch(markers=markers).model_dump_json(),
                usage=TokenUsage(input_tokens=0, response_tokens=0),
            )
