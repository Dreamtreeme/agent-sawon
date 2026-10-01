"""OCR line groups are evidence extents, never inferred input/control rectangles."""

from __future__ import annotations

from pipeline.schemas import OcrBlock

from .scene import contains


def _bounds(blocks: list[OcrBlock]) -> tuple[int, int, int, int]:
    x = min(b.bbox[0] for b in blocks)
    y = min(b.bbox[1] for b in blocks)
    return (
        x,
        y,
        max(b.bbox[0] + b.bbox[2] for b in blocks) - x,
        max(b.bbox[1] + b.bbox[3] for b in blocks) - y,
    )


def row_candidates(blocks: list[OcrBlock], containers: list[tuple]) -> list[tuple]:
    """Group aligned OCR spans in repeated lines inside a measured container.

    No record values or application labels are consulted. Semantic classification
    still determines whether a candidate is a data row, header or unrelated text.
    Group bounds are the union of actual text; clipped text is never completed.
    """
    groups = {}
    for box in containers:
        lines: list[list[OcrBlock]] = []
        for block in sorted(
            (b for b in blocks if contains(box, b.bbox)), key=lambda b: (b.bbox[1], b.bbox[0])
        ):
            _, y, _, h = block.bbox
            for line in lines:
                _, a, _, d = _bounds(line)
                if max(0, min(y + h, a + d) - max(y, a)) >= 0.65 * min(h, d):
                    line.append(block)
                    break
            else:
                lines.append([block])
        lines = [sorted(line, key=lambda b: b.bbox[0]) for line in lines if len(line) >= 2]
        for line in lines:
            # Require repeated columns, not just two unrelated text captions.
            if not any(
                sum(
                    any(
                        abs(b.bbox[0] - other.bbox[0]) <= max(b.bbox[3], other.bbox[3])
                        for other in adjacent
                    )
                    for b in line
                )
                >= 2
                for adjacent in lines
                if adjacent is not line
            ):
                continue
            extent = _bounds(line)
            groups[extent] = tuple(b.id for b in line)
    return sorted(groups.items(), key=lambda item: (item[0][1], item[0][0]))
