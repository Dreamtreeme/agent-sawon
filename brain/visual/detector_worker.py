"""독립 프로세스로 실행되는 OmniParser v2 좌표 검출 워커."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from time import perf_counter


def original_box(coords: list[float], original: tuple[int, int], resized: tuple[int, int]):
    """축별 정수 리사이즈를 되돌려 원본 픽셀 [left, top, width, height] 반환."""
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


def restore_region_box(
    coords: list[float],
    region: tuple[int, int, int, int],
    resized: tuple[int, int],
    full_size: tuple[int, int],
) -> list[int] | None:
    ox, oy, width, height = region
    x, y, w, h = original_box(coords, (width, height), resized)
    # 크롭 영역의 잘린 테두리에 걸친 불완전한 상자는 제외
    if (
        (ox > 0 and x <= 1)
        or (oy > 0 and y <= 1)
        or (ox + width < full_size[0] and x + w >= width - 1)
        or (oy + height < full_size[1] and y + h >= height - 1)
    ):
        return None
    return [ox + x, oy + y, w, h]


class DetectorSession:
    def __init__(self):
        self.model = None
        self.weights = None
        self.metrics = {}

    def __call__(self, request):
        from PIL import Image

        weights = Path(request["weights"])
        if not weights.is_file():
            raise ValueError("omniparser_weights_missing")

        device = request.get("device", "cpu")
        initialized = self.model is None or self.weights != weights
        started = perf_counter()

        if initialized:
            from ultralytics import YOLO

            self.model = YOLO(str(weights))
            self.weights = weights

        initialization_ms = (perf_counter() - started) * 1000
        started = perf_counter()

        with Image.open(request["image"]) as source:
            image = source.convert("RGB")

        boxes = []
        full_size = image.size
        regions = request.get("regions", [])
        if len(regions) > 6:
            raise ValueError("refinement_region_limit")

        for region in regions or [(0, 0, *full_size)]:
            ox, oy, width, height = region
            if not (0 <= ox < ox + width <= full_size[0] and 0 <= oy < oy + height <= full_size[1]):
                raise ValueError("refinement_region_outside_image")

            view = image.crop((ox, oy, ox + width, oy + height))
            scale = (
                min(request["refinement_scale"], 1280 / max(view.size))
                if regions
                else min(1.0, request["max_dimension"] / max(view.size))
            )
            if scale != 1:
                view = view.resize(
                    tuple(max(1, int(v * scale)) for v in view.size), Image.Resampling.BILINEAR
                )

            results = self.model(
                view,
                conf=request["confidence"],
                verbose=False,
                device=device,
                imgsz=1280 if regions else request.get("image_size", 640),
            )

            for result in results:
                for box in result.boxes:
                    bbox = restore_region_box(
                        box.xyxy[0].cpu().tolist(), region, view.size, full_size
                    )
                    if bbox is not None:
                        boxes.append(
                            {
                                "id": len(boxes),
                                "source": "omniparser",
                                "bbox": bbox,
                                "confidence": float(box.conf.item()),
                            }
                        )

        detection_ms = (perf_counter() - started) * 1000
        self.metrics = {
            "device": str(getattr(getattr(self.model, "predictor", None), "device", device)),
            "model_initialized": initialized,
            "model_init_ms": initialization_ms if initialized else 0,
            "detection_ms": detection_ms,
            "inference_ms": detection_ms,
        }
        return boxes


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "--serve":
        raise SystemExit("Usage: detector_worker.py --serve YOLO_CONFIG_DIR")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
    from worker_server import serve

    os.environ["YOLO_CONFIG_DIR"] = sys.argv[2]
    serve(DetectorSession())


if __name__ == "__main__":
    main()
