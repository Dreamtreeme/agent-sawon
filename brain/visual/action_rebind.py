"""Rebind a planned visual target to a fresh capture using local evidence only."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from sawon_interfaces import UIElement

from brain.observe.values import compact_normalize
from sawon_runtime.visual import OcrBlock

from .markers import Marker
from .scene import SceneObservation, overlap


@dataclass(frozen=True)
class ActionTargetBinding:
    point: tuple[float, float]
    bbox: tuple[int, int, int, int]
    method: str


def perceptual_hash(path: Path, *, size: int = 32, low_frequency: int = 8) -> int:
    """Return a 64-bit DCT perceptual hash without a new runtime dependency."""
    with Image.open(path) as source:
        image = source.convert("L").resize((size, size), Image.Resampling.LANCZOS)
        pixels = list(image.get_flattened_data())
    coefficients = []
    scale = math.pi / (2 * size)
    for vertical in range(low_frequency):
        for horizontal in range(low_frequency):
            value = 0.0
            for y in range(size):
                cy = math.cos((2 * y + 1) * vertical * scale)
                row = y * size
                for x in range(size):
                    value += pixels[row + x] * cy * math.cos((2 * x + 1) * horizontal * scale)
            coefficients.append(value)
    median_source = sorted(coefficients[1:])
    median = median_source[len(median_source) // 2]
    result = 0
    for coefficient in coefficients:
        result = (result << 1) | int(coefficient > median)
    return result


def hash_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _center(box: tuple[int, int, int, int]) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _matching_text(blocks: list[OcrBlock], anchors: set[str]) -> list[OcrBlock]:
    exact = [block for block in blocks if compact_normalize(block.text) in anchors]
    if exact:
        return exact
    return [
        block
        for block in blocks
        if any(
            len(anchor) >= 2
            and (anchor in compact_normalize(block.text) or compact_normalize(block.text) in anchor)
            for anchor in anchors
        )
    ]


def relative_point(
    old_box: tuple[float, float, float, float],
    new_box: tuple[int, int, int, int],
    point: tuple[float, float] | None,
) -> tuple[float, float]:
    if point is None:
        ratio_x = ratio_y = 0.5
    else:
        # An observed caption can anchor an action point outside its own rectangle.
        ratio_x = (point[0] - old_box[0]) / max(1, old_box[2])
        ratio_y = (point[1] - old_box[1]) / max(1, old_box[3])
    return new_box[0] + new_box[2] * ratio_x, new_box[1] + new_box[3] * ratio_y


def rebind_action_target(
    scene: SceneObservation,
    target: UIElement,
    *,
    fresh_size: tuple[int, int],
    blocks: list[OcrBlock],
    markers: list[Marker],
    planned_point: tuple[float, float] | None = None,
) -> ActionTargetBinding:
    """Find one current target from its visible text and local control geometry."""
    if target.role.casefold() not in {"edit", "combobox"}:
        anchors = {compact_normalize(target.name), compact_normalize(target.label_hint)} - {
            "",
            "[redacted]",
        }
        if not anchors:
            raise ValueError("visual_target_has_no_local_anchor")
        text_matches = _matching_text(blocks, anchors)
        if not text_matches:
            raise ValueError("visual_target_anchor_missing")
        if len(text_matches) != 1:
            raise ValueError("visual_target_anchor_ambiguous")
        box = text_matches[0].bbox
        return ActionTargetBinding(
            relative_point(target.bbox, box, planned_point), box, "ocr_text_anchor"
        )

    fresh_width, fresh_height = fresh_size
    scale_x = fresh_width / scene.screenshot.width
    scale_y = fresh_height / scene.screenshot.height
    x, y = planned_point or _center(target.bbox)
    point = (x * scale_x, y * scale_y)
    candidates = []
    for marker in markers:
        left, top, width, height = marker.bbox
        if (
            marker.source == "omniparser"
            and width * height <= fresh_width * fresh_height * 0.5
            and left <= point[0] < left + width
            and top <= point[1] < top + height
        ):
            candidates.append(marker.bbox)
    if not candidates:
        raise ValueError("visual_input_control_not_found")
    if len(candidates) > 1:
        left, top, width, height = target.bbox
        projected = (left * scale_x, top * scale_y, width * scale_x, height * scale_y)
        ranked = sorted(((overlap(projected, box), box) for box in candidates), reverse=True)
        if ranked[0][0] - ranked[1][0] < 0.12:
            raise ValueError("visual_input_control_ambiguous")
        return ActionTargetBinding(point, ranked[0][1], "selected_input_region")
    return ActionTargetBinding(point, candidates[0], "selected_input_region")
