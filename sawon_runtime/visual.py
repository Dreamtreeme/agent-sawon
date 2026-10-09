"""Shared immutable visual contracts without worker or pipeline ownership."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

DocumentKind = Literal["hbl", "invoice", "packing_list"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OcrBlock(FrozenModel):
    id: int = Field(ge=0)
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    # Pixel coordinates on the rendered page: x, y, width, height.
    bbox: tuple[int, int, int, int]
    input_variant: Literal["unspecified", "original", "median"] = "unspecified"

    @model_validator(mode="after")
    def valid_bbox(self) -> Self:
        x, y, width, height = self.bbox
        if min(x, y) < 0 or min(width, height) <= 0:
            raise ValueError("OCR bounds must have nonnegative origin and positive size")
        return self


class RenderedPage(FrozenModel):
    document: str
    kind: DocumentKind
    page: int = Field(ge=1)
    image_path: Path
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class OcrEngine(Protocol):
    def read(self, page: RenderedPage) -> list[OcrBlock]: ...
