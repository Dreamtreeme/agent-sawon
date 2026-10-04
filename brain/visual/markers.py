"""Local detector boxes are authoritative; the model can only name marker IDs."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pipeline.schemas import OcrBlock

from .row_candidates import row_candidates
from .scene import Detection, SemanticDetection, VisualControl, contains, overlap


class Marker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: int = Field(ge=0)
    bbox: tuple[int, int, int, int]
    source: Literal["ocr", "ocr_group", "omniparser", "pixel_rectangle"]
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
    candidates += [
        Marker(
            id=0,
            bbox=extent,
            source="ocr_group",
            confidence=min(b.confidence for b in blocks if b.id in ids),
            text_ids=ids,
        )
        for extent, ids in row_candidates(blocks, [box.bbox for box in kept])
    ]
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


def marker_role_allowed(source: str, role: str, *, allow_ocr_inputs: bool = False) -> bool:
    """Shared geometry capabilities for semantic questions and grounding."""
    if source == "ocr_group":
        return role in {"row", "header", "group", "text"}
    if source == "ocr":
        disallowed = {"table", "group", "window"}
        if not allow_ocr_inputs:
            disallowed.update({"edit", "combobox", "row"})
        return role not in disallowed
    return True


def nested_rectangle_count(marker: Marker, markers: list[Marker]) -> int:
    """Count materially smaller closed rectangles inside one rectangle.

    A desktop text input can contain OCR, but a large frame containing several
    independently detected rectangles is a container/grid candidate.  It must
    not become the writable extent for a nearby field whose own boundary is
    clipped outside the capture.
    """
    if marker.source != "pixel_rectangle":
        return 0
    area = marker.bbox[2] * marker.bbox[3]
    return sum(
        child.source == "pixel_rectangle"
        and child.id != marker.id
        and child.bbox[2] * child.bbox[3] <= area * 0.6
        and contains(marker.bbox, child.bbox)
        for child in markers
    )


def ground_meanings(
    meanings: SemanticDetection,
    markers: list[Marker],
    blocks: list[OcrBlock],
    *,
    allow_ocr_inputs: bool = False,
) -> Detection:
    by_id = {m.id: m for m in markers}
    if len(by_id) != len(markers):
        raise ValueError("duplicate_marker_id")
    used: set[int] = set()
    controls = []
    uncertainties = list(meanings.uncertainties)
    for meaning in meanings.controls:
        if meaning.id not in by_id or meaning.id in used:
            raise ValueError("unknown_or_duplicate_marker_id")
        used.add(meaning.id)
        marker = by_id[meaning.id]
        if not marker_role_allowed(marker.source, meaning.role, allow_ocr_inputs=allow_ocr_inputs):
            raise ValueError(
                "ocr_group_is_not_input_or_table_extent"
                if marker.source == "ocr_group"
                else "ocr_text_is_not_control_extent"
            )
        # OCR geometry may stand in for the literal value of an input only while
        # that value is visibly focused.  Otherwise it is just text evidence:
        # treating a nearby caption as a writable surface sends input to the
        # caption's centre instead of to an observed control extent.
        if (
            marker.source == "ocr"
            and meaning.role in {"edit", "combobox"}
            and allow_ocr_inputs
            and not meaning.focused
        ):
            raise ValueError("ocr_text_is_not_control_extent")
        if meaning.role in {"edit", "combobox"} and nested_rectangle_count(marker, markers) >= 2:
            raise ValueError("nested_rectangle_container_is_not_input")
        # Literal value evidence is constrained to the measured box. OCR markers
        # cannot be relabelled with a different OCR block. Omitted links are
        # already known locally; asking the model to reproduce them adds no evidence.
        if marker.source == "ocr" and not meaning.text_ids:
            meaning = meaning.model_copy(update={"text_ids": list(marker.text_ids)})
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
            rows = [c for c in controls if c.role == "row" and contains(c.bbox, marker.bbox)]
            controls.append(
                VisualControl(
                    id=marker.id,
                    role="cell" if len(rows) == 1 else "text",
                    parent_id=rows[0].id if len(rows) == 1 else None,
                    bbox=list(marker.bbox),
                    text_ids=list(marker.text_ids),
                    confidence=marker.confidence,
                )
            )
    grounded_controls = []
    for control in controls:
        valid_text_ids = [
            block.id
            for block in blocks
            if block.id in control.text_ids and contains(control.bbox, block.bbox)
        ]
        if len(valid_text_ids) != len(control.text_ids):
            # A value relationship is evidence, not geometry. Preserve the locally
            # grounded control/action target and withhold only the unsupported value.
            uncertainties.append(f"control:{control.id}:value_outside_local_marker")
            control = control.model_copy(update={"text_ids": valid_text_ids})
        grounded_controls.append(control)
    return Detection(
        screen_kind=meanings.screen_kind,
        controls=grounded_controls,
        uncertainties=uncertainties,
    )
