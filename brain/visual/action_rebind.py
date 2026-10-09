"""Rebind a planned visual target to a fresh capture using local evidence only."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from sawon_interfaces import UIElement

from sawon_runtime.visual import OcrBlock

from .markers import Marker
from .scene import SceneObservation


@dataclass(frozen=True)
class ActionTargetBinding:
    point: tuple[float, float]
    bbox: tuple[int, int, int, int]
    method: str
    phash_distance: int


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


def _normalized(value: str | None) -> str:
    return "".join((value or "").split()).casefold()


def _center(box: tuple[int, int, int, int]) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _old_label(scene: SceneObservation, target: UIElement, anchors: set[str]) -> UIElement | None:
    matches = [
        item
        for item in scene.elements
        if item.ref != target.ref
        and item.role.casefold() in {"text", "label", "cell", "header", "headeritem"}
        and _normalized(item.name) in anchors
    ]
    return matches[0] if len(matches) == 1 else None


def _matching_text(blocks: list[OcrBlock], anchors: set[str]) -> list[OcrBlock]:
    exact = [block for block in blocks if _normalized(block.text) in anchors]
    if exact:
        return exact
    return [
        block
        for block in blocks
        if any(
            len(anchor) >= 2
            and (anchor in _normalized(block.text) or _normalized(block.text) in anchor)
            for anchor in anchors
        )
    ]


def _relative_point(
    old_box: tuple[float, float, float, float],
    new_box: tuple[int, int, int, int],
    point: tuple[float, float] | None,
) -> tuple[float, float]:
    if point is None:
        ratio_x = ratio_y = 0.5
    else:
        ratio_x = min(1.0, max(0.0, (point[0] - old_box[0]) / max(1, old_box[2])))
        ratio_y = min(1.0, max(0.0, (point[1] - old_box[1]) / max(1, old_box[3])))
    return new_box[0] + new_box[2] * ratio_x, new_box[1] + new_box[3] * ratio_y


def rebind_action_target(
    scene: SceneObservation,
    target: UIElement,
    *,
    old_capture: Path,
    fresh_capture: Path,
    fresh_size: tuple[int, int],
    blocks: list[OcrBlock],
    markers: list[Marker],
    planned_point: tuple[float, float] | None = None,
    max_phash_distance: int = 16,
) -> ActionTargetBinding:
    """Find one current target from its visible text and local control geometry."""
    distance = hash_distance(perceptual_hash(old_capture), perceptual_hash(fresh_capture))
    if distance > max_phash_distance:
        raise ValueError("visual_scene_family_changed")
    anchors = {_normalized(target.name), _normalized(target.label_hint)} - {"", "[redacted]"}
    if not anchors:
        raise ValueError("visual_target_has_no_local_anchor")
    text_matches = _matching_text(blocks, anchors)
    role = target.role.casefold()
    if role not in {"edit", "combobox"}:
        if len(text_matches) != 1:
            raise ValueError("visual_target_anchor_ambiguous")
        box = text_matches[0].bbox
        return ActionTargetBinding(
            _relative_point(target.bbox, box, planned_point), box, "ocr_text_anchor", distance
        )

    if len(text_matches) != 1:
        raise ValueError("visual_input_label_ambiguous")
    label = text_matches[0]
    old_label = _old_label(scene, target, anchors)
    old_width, old_height = scene.screenshot.width, scene.screenshot.height
    fresh_width, fresh_height = fresh_size
    scale_x, scale_y = fresh_width / old_width, fresh_height / old_height
    target_center = _center(target.bbox)
    if old_label is None:
        expected = target_center[0] * scale_x, target_center[1] * scale_y
    else:
        old_label_center = _center(old_label.bbox)
        fresh_label_center = _center(label.bbox)
        expected = (
            fresh_label_center[0] + (target_center[0] - old_label_center[0]) * scale_x,
            fresh_label_center[1] + (target_center[1] - old_label_center[1]) * scale_y,
        )
    expected_width = max(1, target.bbox[2] * scale_x)
    expected_height = max(1, target.bbox[3] * scale_y)
    candidates = []
    for marker in markers:
        if marker.source not in {"omniparser"}:
            continue
        x, y, width, height = marker.bbox
        if width * height > fresh_width * fresh_height * 0.5:
            continue
        center = _center(marker.bbox)
        center_error = math.hypot(
            (center[0] - expected[0]) / max(30, expected_width),
            (center[1] - expected[1]) / max(20, expected_height),
        )
        shape_error = abs(math.log(max(1, width) / expected_width)) + abs(
            math.log(max(1, height) / expected_height)
        )
        candidates.append((center_error + shape_error * 0.35, marker))
    candidates.sort(key=lambda item: item[0])
    if not candidates or candidates[0][0] > 2.5:
        raise ValueError("visual_input_control_not_found")
    if len(candidates) > 1 and candidates[1][0] - candidates[0][0] < 0.12:
        raise ValueError("visual_input_control_ambiguous")
    box = candidates[0][1].bbox
    return ActionTargetBinding(
        _relative_point(target.bbox, box, planned_point), box, "ocr_label_local_control", distance
    )
