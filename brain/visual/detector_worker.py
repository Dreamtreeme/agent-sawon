"""Standalone local detector runtime (may use an isolated Python environment)."""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path


def original_box(coords: list[float], original: tuple[int, int], resized: tuple[int, int]):
    """Undo actual per-axis integer resize, converting xyxy to original-image xywh."""
    if len(coords) != 4 or not all(math.isfinite(v) for v in coords):
        raise ValueError("invalid_detector_coordinates")
    width, height = original
    sx, sy = width / resized[0], height / resized[1]
    x1, y1, x2, y2 = coords
    if not 0 <= x1 < x2 <= resized[0] or not 0 <= y1 < y2 <= resized[1]:
        raise ValueError("detector_box_outside_image")
    left, top = math.floor(x1 * sx), math.floor(y1 * sy)
    right, bottom = min(width, math.ceil(x2 * sx)), min(height, math.ceil(y2 * sy))
    return [left, top, right - left, bottom - top]


def main() -> None:
    request = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    os.environ["YOLO_CONFIG_DIR"] = str(Path(sys.argv[2]).parent / "yolo")
    from PIL import Image
    from ultralytics import YOLO

    weights = Path(request["weights"])
    if not weights.is_file():
        raise ValueError("omniparser_weights_missing")
    with Image.open(request["image"]) as source:
        image = source.convert("RGB")
    original = image.size
    scale = min(1.0, request["max_dimension"] / max(original))
    if scale != 1:
        image = image.resize(tuple(max(1, int(v * scale)) for v in original))
    model = YOLO(str(weights))
    results = model(image, conf=request["confidence"], verbose=False, device="cpu")
    boxes = []
    for result in results:
        for box in result.boxes:
            boxes.append(
                {
                    "id": len(boxes),
                    "source": "omniparser",
                    "bbox": original_box(box.xyxy[0].cpu().tolist(), original, image.size),
                    "confidence": float(box.conf.item()),
                }
            )
    Path(sys.argv[2]).write_text(json.dumps(boxes), encoding="utf-8")


if __name__ == "__main__":
    main()
