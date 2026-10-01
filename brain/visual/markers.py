"""Local detector boxes are authoritative; the model can only name marker IDs."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pipeline.schemas import OcrBlock

from .scene import Detection, SemanticDetection, VisualControl, contains, overlap


class Marker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: int = Field(ge=0)
    bbox: tuple[int, int, int, int]
    source: Literal["ocr", "omniparser", "pixel_rectangle"]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    text_ids: tuple[int, ...] = ()


class MergeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    min_ocr_confidence: float = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)
    text_overlap: float = Field(default=0.85, gt=0, le=1, allow_inf_nan=False)
    text_padding_ratio: float = Field(default=0.15, ge=0, le=0.3, allow_inf_nan=False)
    max_text_padding: int = Field(default=4, ge=0, le=8)
    duplicate_iou: float = Field(default=0.85, gt=0, le=1, allow_inf_nan=False)
    nested_area_ratio: float = Field(default=0.45, gt=0, le=1, allow_inf_nan=False)


def intersection(left: tuple, right: tuple) -> float:
    x, y, w, h = left
    a, b, c, d = right
    return max(0, min(x + w, a + c) - max(x, a)) * max(0, min(y + h, b + d) - max(y, b))


def is_text_fragment(box: Marker, block: OcrBlock, settings: MergeSettings) -> bool:
    if block.confidence < settings.min_ocr_confidence:
        return False
    x, y, w, h = box.bbox
    a, b, c, d = block.bbox
    padding = min(settings.max_text_padding, round(d * settings.text_padding_ratio))
    area = intersection(box.bbox, (a - padding, b - padding, c + 2 * padding, d + 2 * padding))
    # Padding accounts for OCR line boxes versus individual glyph extents. A
    # genuinely larger control enclosing the text must survive this filter.
    return h <= d * 1.5 and w * h <= c * d * 1.2 and area / (w * h) >= settings.text_overlap


def duplicate_detection(left: Marker, right: Marker, settings: MergeSettings) -> bool:
    if overlap(left.bbox, right.bbox) >= settings.duplicate_iou:
        return True
    x, y, w, h = left.bbox
    a, b, c, d = right.bbox
    smaller, larger = sorted((w * h, c * d))
    # Near-concentric nested icon boxes are duplicates, but a field/table and
    # its much smaller child are not. OCR boxes are never passed to this rule.
    return (
        smaller / larger >= settings.nested_area_ratio
        and intersection(left.bbox, right.bbox) / smaller >= 0.9
        and abs(x + w / 2 - a - c / 2) <= min(w, c) * 0.2
        and abs(y + h / 2 - b - d / 2) <= min(h, d) * 0.2
    )


def build_markers(
    blocks: list[OcrBlock],
    boxes: list[Marker],
    width: int,
    height: int,
    *,
    settings: MergeSettings | None = None,
) -> list[Marker]:
    """Keep OCR anchors and genuine enclosing controls; suppress duplicate detections.

    Unlike L2C's icon-only merge, a larger detector box enclosing text is useful
    for desktop inputs and tables. Never discard the OCR evidence itself.
    """
    settings = settings or MergeSettings()
    candidates = [
        Marker(id=0, bbox=b.bbox, source="ocr", confidence=b.confidence, text_ids=(b.id,))
        for b in blocks
    ]
    kept: list[Marker] = []
    for box in sorted(boxes, key=lambda b: -b.confidence):
        x, y, w, h = box.bbox
        if box.source not in {"omniparser", "pixel_rectangle"} or box.text_ids:
            raise ValueError("invalid_detector_marker_source")
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
            raise ValueError("local_marker_outside_capture")

        # OmniParser sometimes calls individual glyphs icons. Remove detections
        # substantially inside an OCR span, but retain larger enclosing controls.
        if any(is_text_fragment(box, block, settings) for block in blocks):
            continue
        if any(duplicate_detection(box, other, settings) for other in kept):
            continue
        kept.append(box)
    candidates += kept
    if len(candidates) > 500:
        raise ValueError("visual_marker_limit")
    result = []
    for index, marker in enumerate(sorted(candidates, key=lambda m: (m.bbox[1], m.bbox[0]))):
        x, y, w, h = marker.bbox
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
            raise ValueError("local_marker_outside_capture")
        result.append(marker.model_copy(update={"id": index}))
    return result


def render_markers(path: Path, output: Path, markers: list[Marker]) -> None:
    """Render numbered model-input evidence without altering the raw capture."""
    from PIL import Image, ImageDraw

    with Image.open(path) as original:
        marked = original.convert("RGB")
    draw = ImageDraw.Draw(marked)
    for marker in markers:
        x, y, w, h = marker.bbox
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline="red", width=1)
        draw.text(
            (x, max(0, y - 12)), str(marker.id), fill="red", stroke_width=1, stroke_fill="white"
        )
    marked.save(output)


def ground_meanings(
    meanings: SemanticDetection, markers: list[Marker], blocks: list[OcrBlock]
) -> Detection:
    by_id = {m.id: m for m in markers}
    if len(by_id) != len(markers):
        raise ValueError("duplicate_marker_id")
    used: set[int] = set()
    controls = []
    for meaning in meanings.controls:
        if meaning.id not in by_id or meaning.id in used:
            raise ValueError("unknown_or_duplicate_marker_id")
        used.add(meaning.id)
        marker = by_id[meaning.id]
        if marker.source == "ocr" and meaning.role in {
            "edit",
            "combobox",
            "table",
            "row",
            "group",
            "window",
        }:
            raise ValueError("ocr_text_is_not_control_extent")
        # Literal value evidence is constrained to the measured box. OCR markers
        # cannot be relabelled with a different OCR block or omit their own text.
        if marker.source == "ocr" and set(meaning.text_ids) != set(marker.text_ids):
            raise ValueError("ocr_marker_text_mismatch")
        if meaning.role in {"edit", "combobox"} and not meaning.text_ids:
            meaning = meaning.model_copy(
                update={"text_ids": [b.id for b in blocks if contains(marker.bbox, b.bbox)]}
            )
        controls.append(VisualControl(**meaning.model_dump(), bbox=list(marker.bbox)))
    # Unclassified text remains visible as static evidence, never an input target.
    for marker in markers:
        if marker.id not in used and marker.source == "ocr":
            controls.append(
                VisualControl(
                    id=marker.id,
                    role="text",
                    bbox=list(marker.bbox),
                    text_ids=list(marker.text_ids),
                    confidence=marker.confidence,
                )
            )
    for control in controls:
        for block in blocks:
            if block.id in control.text_ids and not contains(control.bbox, block.bbox):
                raise ValueError("value_outside_local_marker")
    return Detection(
        screen_kind=meanings.screen_kind, controls=controls, uncertainties=meanings.uncertainties
    )
