"""Image-only rectangular boundaries; these candidates do not imply semantic roles."""

from __future__ import annotations


def rectangular_candidates(image, *, limit: int = 80) -> list[list[int]]:
    """Find closed line boundaries, retaining nested grid cells and their outer frame.

    No OCR labels, application metadata, or expected layout enter this function.
    Open lines and image-edge cutoffs are not complete control extents.
    """
    import cv2
    import numpy as np

    pixels = np.asarray(image.convert("L"))
    height, width = pixels.shape
    binary = (pixels < 110).astype("uint8") * 255
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, np.ones((1, (max(24, width // 32) | 1)), np.uint8)
    )
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((17, 1), np.uint8))
    lines = cv2.bitwise_or(horizontal, vertical)
    contours, _ = cv2.findContours(lines, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = set()
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < 48 or h < 24 or x <= 0 or y <= 0 or x + w >= width or y + h >= height:
            continue
        # Every side must have line evidence. A contour around three connected
        # sides, a caption underline, or a cropped frame is insufficient.
        margin = 2
        top = horizontal[max(0, y - margin) : y + margin + 1, x : x + w]
        bottom = horizontal[y + h - margin - 1 : y + h + margin, x : x + w]
        left = vertical[y : y + h, max(0, x - margin) : x + margin + 1]
        right = vertical[y : y + h, x + w - margin - 1 : x + w + margin]
        coverage = [
            np.any(top, axis=0).mean(),
            np.any(bottom, axis=0).mean(),
            np.any(left, axis=1).mean(),
            np.any(right, axis=1).mean(),
        ]
        if min(coverage) >= 0.9:
            candidates.add((x, y, w, h))
    # Large frames before their nearly identical inner contours, then cells.
    return [list(box) for box in sorted(candidates, key=lambda b: (-b[2] * b[3], b))[:limit]]
