"""독립 프로세스로 실행되는 OmniParser (YOLO + 선택적 Florence-2) 추론 워커."""

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


def overlaps_ocr(box: list[int], text_boxes: list) -> bool:
    """OCR already supplies text, including glyph fragments inside a text line."""
    x, y, width, height = box
    for a, b, w, h in text_boxes:
        intersection = max(0, min(x + width, a + w) - max(x, a)) * max(
            0, min(y + height, b + h) - max(y, b)
        )
        if intersection / min(width * height, w * h) >= 0.8:
            return True
    return False


class DetectorSession:
    def __init__(self):
        self.model = None
        self.weights = None
        self.caption_model = None
        self.caption_processor = None
        self.caption_weights = None
        self.metrics = {}

    def _init_caption_model(self, caption_path: str | None, device: str) -> bool:
        """Load the configured local Florence model once; never silently omit v2 captions."""
        if not caption_path or not Path(caption_path).is_dir():
            raise ValueError("omniparser_caption_weights_missing")
        key = (str(Path(caption_path).resolve()), device)
        if self.caption_model is not None and self.caption_weights == key:
            return False
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        processor = AutoProcessor.from_pretrained(
            caption_path, trust_remote_code=True, local_files_only=True
        )
        model = (
            AutoModelForCausalLM.from_pretrained(
                caption_path,
                trust_remote_code=True,
                local_files_only=True,
                torch_dtype=torch.float16 if device.startswith("cuda") else torch.float32,
                attn_implementation="eager",
            )
            .to(device)
            .eval()
        )
        self.caption_processor, self.caption_model = processor, model
        self.caption_weights = key
        return True

    def _caption_boxes(self, image, boxes: list[dict], batch_size: int) -> None:
        """Batch the official v2 64px icon crops on the persistent GPU model."""
        import torch
        from PIL import Image

        with torch.inference_mode():
            for offset in range(0, len(boxes), batch_size):
                batch = boxes[offset : offset + batch_size]
                crops = []
                for box in batch:
                    x, y, width, height = box["bbox"]
                    crops.append(
                        image.crop((x, y, x + width, y + height)).resize(
                            (64, 64), Image.Resampling.BILINEAR
                        )
                    )
                inputs = self.caption_processor(
                    text=["<CAPTION>"] * len(crops),
                    images=crops,
                    return_tensors="pt",
                    do_resize=False,
                ).to(device=self.caption_model.device, dtype=self.caption_model.dtype)
                generated = self.caption_model.generate(
                    # Florence builds the mask after adding visual tokens. The
                    # processor's text-only mask does not cover that sequence.
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=20,
                    num_beams=1,
                    do_sample=False,
                )
                descriptions = self.caption_processor.batch_decode(
                    generated, skip_special_tokens=True
                )
                for box, description in zip(batch, descriptions, strict=True):
                    box["functional_description"] = description.strip()[:160] or None

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

        caption_initialized = False
        if request.get("use_v2"):
            caption_initialized = self._init_caption_model(request.get("caption_weights"), device)

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
                imgsz=1280 if regions or request.get("use_v2") else 640,
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
                                "functional_description": None,
                            }
                        )

        detection_ms = (perf_counter() - started) * 1000
        started = perf_counter()
        caption_targets = [
            box for box in boxes if not overlaps_ocr(box["bbox"], request.get("ocr_boxes", []))
        ]
        if request.get("use_v2") and caption_targets:
            self._caption_boxes(image, caption_targets, request.get("caption_batch_size", 16))
        caption_ms = (perf_counter() - started) * 1000 if request.get("use_v2") else 0
        self.metrics = {
            "device": str(getattr(getattr(self.model, "predictor", None), "device", device)),
            "model_initialized": initialized,
            "model_init_ms": initialization_ms if initialized or caption_initialized else 0,
            "caption_initialized": caption_initialized,
            "caption_enabled": bool(request.get("use_v2")),
            "ocr_caption_skipped": len(boxes) - len(caption_targets),
            "captioned_boxes": sum(bool(box["functional_description"]) for box in boxes),
            "detection_ms": detection_ms,
            "caption_ms": caption_ms,
            "inference_ms": detection_ms + caption_ms,
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
