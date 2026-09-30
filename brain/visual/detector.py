"""Bounded local OmniParser inference; no network or LLM coordinate fallback."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from models.base import EventSink, MeasuredModelClient, ProviderReply, TokenUsage
from pipeline.persistent_worker import PersistentWorker
from pipeline.schemas import RenderedPage

from .markers import Marker


class DetectorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    weights: Path = Path("models/omniparser/icon_detect/model.pt")
    python: str | None = None
    device: str = "cpu"
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
        self._worker = None
        self.last_metrics = {}

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
        if not images or len(images) != 1:
            raise ValueError("detector_requires_one_image")
        if not self.settings.weights.is_file():
            raise ValueError("omniparser_weights_missing")
        if self._worker is None:
            self._worker = PersistentWorker(
                [
                    self.settings.python or sys.executable,
                    str(Path(__file__).with_name("detector_worker.py")),
                    "--serve",
                    str(images[0].parent / "yolo-runtime"),
                ],
                directory=images[0].parent,
            )
        response = self._worker.request(
            {
                "image": str(images[0].resolve()),
                "weights": str(self.settings.weights.resolve()),
                "device": self.settings.device,
                "confidence": self.settings.confidence,
                "max_dimension": self.settings.max_dimension,
                "regions": json.loads(prompt)["regions"],
                "refinement_scale": self.settings.refinement_scale,
            },
            timeout=self.settings.timeout_seconds,
        )
        self.last_metrics = self._worker.last_metrics
        markers = [Marker.model_validate(b) for b in response]
        return ProviderReply(
            text=MarkerBatch(markers=markers).model_dump_json(),
            usage=TokenUsage(input_tokens=0, response_tokens=0),
        )

    def close(self) -> None:
        if self._worker is not None:
            self._worker.close()
