"""로컬 검출기 상자와 OCR 텍스트를 결합하여 단일 마커 번호공간을 부여하는 모듈."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sawon_runtime.visual import OcrBlock

from .row_candidates import row_candidates
from .scene import Detection, SemanticDetection, VisualControl, contains, overlap


class Marker(BaseModel):
    """화면 상의 로컬 시각 검출 영역 단위."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: int = Field(ge=0)
    bbox: tuple[int, int, int, int]
    source: Literal["ocr", "ocr_group", "omniparser"]
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
    # Detector boxes that only pad an OCR glyph vertically are text fragments,
    # not writable controls. Compare each axis: an area-only limit retained tall
    # label boxes such as the Korean "수량" marker and let them masquerade as
    # an adjacent input. Wider button/input extents remain available.
    return h <= d * 1.5 and w <= c * 1.2 and area / (w * h) >= settings.text_overlap


def duplicate_detection(left: Marker, right: Marker, settings: MergeSettings) -> bool:
    if overlap(left.bbox, right.bbox) >= settings.duplicate_iou:
        return True
    x, y, w, h = left.bbox
    a, b, c, d = right.bbox
    smaller, larger = sorted((w * h, c * d))
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
    """OCR 텍스트 영역과 탐지된 로컬 상자들을 병합하고 유일한 번호를 부여."""
    settings = settings or MergeSettings()
    candidates = [
        Marker(
            id=0,
            bbox=b.bbox,
            source="ocr",
            confidence=b.confidence,
            text_ids=(b.id,),
        )
        for b in blocks
    ]
    kept: list[Marker] = []
    for box in sorted(boxes, key=lambda b: -b.confidence):
        x, y, w, h = box.bbox
        if box.source != "omniparser" or box.text_ids:
            raise ValueError("invalid_detector_marker_source")
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
            raise ValueError("local_marker_outside_capture")

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
    # y축(상단) 우선, x축(좌측) 차순으로 화면 표시 순서 정렬
    for index, marker in enumerate(sorted(candidates, key=lambda m: (m.bbox[1], m.bbox[0]))):
        x, y, w, h = marker.bbox
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
            raise ValueError("local_marker_outside_capture")
        result.append(marker.model_copy(update={"id": index}))
    return result


def render_markers(path: Path, output: Path, markers: list[Marker]) -> None:
    """디버깅 및 시각 판단용 번호 표시 이미지 생성."""
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
    """각 마커 출처별로 허용되는 UI 역할 규칙."""
    if source == "ocr_group":
        return role in {"row", "header", "group", "text"}
    if source == "ocr":
        disallowed = {"table", "group", "window"}
        if not allow_ocr_inputs:
            disallowed.update({"edit", "combobox", "row"})
        return role not in disallowed
    return True


def _label_is_near_input(
    control: tuple[int, int, int, int], label: tuple[int, int, int, int]
) -> bool:
    """Reject only grossly inconsistent input/label bindings.

    The semantic model may choose any locally detected marker, but an input label
    still has to be spatially related to that marker.  The deliberately generous
    bound permits wide fields and ordinary form spacing while rejecting title-bar
    or window-chrome markers paired with a label on the opposite side of the
    screen.
    """

    x, y, width, height = control
    a, b, label_width, label_height = label
    horizontal_gap = max(0, x - (a + label_width), a - (x + width))
    vertical_gap = max(0, y - (b + label_height), b - (y + height))
    limit = max(256, width * 2, height * 4, label_width * 2, label_height * 4)
    return horizontal_gap * horizontal_gap + vertical_gap * vertical_gap <= limit * limit


def ground_meanings(
    meanings: SemanticDetection,
    markers: list[Marker],
    blocks: list[OcrBlock],
    *,
    allow_ocr_inputs: bool = False,
) -> Detection:
    by_id = {m.id: m for m in markers}
    blocks_by_id = {block.id: block for block in blocks}
    if len(by_id) != len(markers):
        raise ValueError("duplicate_marker_id")
    semantic_ids = {meaning.id for meaning in meanings.controls}
    omitted_structures = {
        meaning.id
        for meaning in meanings.controls
        if meaning.id in by_id
        and meaning.role in {"table", "group", "window"}
        and not marker_role_allowed(
            by_id[meaning.id].source,
            meaning.role,
            allow_ocr_inputs=allow_ocr_inputs,
        )
    }
    seen: set[int] = set()
    used: set[int] = set()
    controls = []
    uncertainties = list(meanings.uncertainties)
    redundant_ocr_inputs = {
        meaning.id: enclosing.id
        for meaning in meanings.controls
        if meaning.id in by_id
        and by_id[meaning.id].source == "ocr"
        and meaning.role in {"edit", "combobox"}
        and meaning.text_ids
        for enclosing in meanings.controls
        if enclosing.id != meaning.id
        and enclosing.id in by_id
        and by_id[enclosing.id].source == "omniparser"
        and enclosing.role in {"edit", "combobox"}
        and set(enclosing.text_ids) == set(meaning.text_ids)
        and set(enclosing.label_ids) == set(meaning.label_ids)
        and contains(by_id[enclosing.id].bbox, by_id[meaning.id].bbox)
    }
    for meaning in meanings.controls:
        if meaning.id not in by_id or meaning.id in seen:
            raise ValueError("unknown_or_duplicate_marker_id")
        seen.add(meaning.id)
        marker = by_id[meaning.id]
        if meaning.role in {"edit", "combobox"} and any(
            label_id in blocks_by_id
            and not _label_is_near_input(marker.bbox, blocks_by_id[label_id].bbox)
            for label_id in meaning.label_ids
        ):
            raise ValueError("input_label_not_near_control")
        if meaning.id in redundant_ocr_inputs:
            uncertainties.append(
                f"redundant_ocr_input_omitted:{meaning.id}:{redundant_ocr_inputs[meaning.id]}"
            )
            continue
        if meaning.id in omitted_structures:
            uncertainties.append(f"unsupported_structural_role_omitted:{meaning.id}:{meaning.role}")
            continue
        used.add(meaning.id)
        if not marker_role_allowed(marker.source, meaning.role, allow_ocr_inputs=allow_ocr_inputs):
            raise ValueError(
                "ocr_group_is_not_input_or_table_extent"
                if marker.source == "ocr_group"
                else "ocr_text_is_not_control_extent"
            )
        if meaning.parent_id is not None and (
            meaning.parent_id in omitted_structures or meaning.parent_id not in semantic_ids
        ):
            if meaning.role in {"row", "cell"}:
                raise ValueError("invalid_visual_parent_bounds")
            uncertainties.append(f"decorative_parent_omitted:{meaning.id}:{meaning.parent_id}")
            meaning = meaning.model_copy(update={"parent_id": None})
        if marker.source == "ocr" and not meaning.text_ids:
            meaning = meaning.model_copy(update={"text_ids": list(marker.text_ids)})
        if marker.source == "ocr" and set(meaning.text_ids) != set(marker.text_ids):
            raise ValueError("ocr_marker_text_mismatch")
        if meaning.role in {"edit", "combobox"} and not meaning.text_ids:
            meaning = meaning.model_copy(
                update={"text_ids": [b.id for b in blocks if contains(marker.bbox, b.bbox)]}
            )

        controls.append(VisualControl(**meaning.model_dump(), bbox=list(marker.bbox)))

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
            uncertainties.append(f"control:{control.id}:value_outside_local_marker")
            control = control.model_copy(update={"text_ids": valid_text_ids})
        grounded_controls.append(control)
    return Detection(
        screen_kind=meanings.screen_kind,
        controls=grounded_controls,
        uncertainties=uncertainties,
    )
