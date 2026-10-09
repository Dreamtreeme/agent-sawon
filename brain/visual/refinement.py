"""Bounded search regions from unmatched OCR, never inferred click targets."""

from sawon_runtime.visual import OcrBlock

from .markers import Marker, intersection
from .scene import contains


def refinement_regions(
    blocks: list[OcrBlock],
    markers: list[Marker],
    width: int,
    height: int,
    *,
    limit: int = 3,
    min_confidence: float = 0.8,
) -> list[tuple[int, int, int, int]]:
    controls = [m for m in markers if m.source in {"omniparser"}]
    regions: list[tuple[int, int, int, int]] = []
    for block in sorted(blocks, key=lambda b: (-b.confidence, b.bbox[1], b.bbox[0])):
        x, y, w, h = block.bbox
        if block.confidence < min_confidence or w > width * 0.35:
            continue
        found = False
        for marker in controls:
            a, b, c, d = marker.bbox
            if contains(marker.bbox, block.bbox) or (
                c >= w
                and b - h * 0.5 <= y + h / 2 <= b + d + h * 0.5
                and max(0, a - x - w, x - a - c) <= width * 0.7
            ):
                found = True
                break
        if found:
            continue
        # Search above/below and both sides, without assuming a form direction.
        top = max(0, y - max(48, h * 3))
        bottom = min(height, y + h + max(48, h * 3))
        region = (0, top, width, bottom - top)
        if bottom - top >= height * 0.9:
            continue
        if any(
            intersection(region, old) / min(width * (bottom - top), old[2] * old[3]) > 0.6
            for old in regions
        ):
            continue
        regions.append(region)
        if len(regions) >= limit:
            break
    return regions
