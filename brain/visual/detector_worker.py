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


class DetectorSession:
    def __init__(self):
        self.model = None
        self.weights = None
        self.caption_model = None
        self.caption_processor = None
        self.caption_weights = None
        self.metrics = {}

    def _init_caption_model(self, caption_path: str | None, device: str) -> None:
        """Florence-2 캡셔닝 모델 초기화 (실패 시 안전 폴백)."""
        if not caption_path or not Path(caption_path).exists():
            return
        if self.caption_model is not None and self.caption_weights == caption_path:
            return
        try:
            from transformers import AutoModelForCausalLM, AutoProcessor

            self.caption_model = AutoModelForCausalLM.from_pretrained(
                caption_path, trust_remote_code=True
            ).to(device).eval()
            self.caption_processor = AutoProcessor.from_pretrained(
                caption_path, trust_remote_code=True
            )
            self.caption_weights = caption_path
        except Exception as exc:
            # 캡셔닝 의존성 오류 시 워커를 죽이지 않고 감지만 수행
            sys.stderr.write(f"Florence-2 초기화 생략 (폴백 유지): {type(exc).__name__}\n")
            self.caption_model = None
            self.caption_processor = None

    def _caption_crop(self, crop, device: str) -> str:
        """아이콘 영역 이미지 조각에 대한 기능 설명(예: 'search button') 생성."""
        if self.caption_model is None or self.caption_processor is None:
            return ""
        try:
            prompt = "<CAPTION>"
            inputs = self.caption_processor(
                text=prompt, images=crop, return_tensors="pt"
            ).to(device)
            generated_ids = self.caption_model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=30,
                num_beams=2,
            )
            decoded = self.caption_processor.batch_decode(
                generated_ids, skip_special_tokens=True
            )
            return decoded[0].strip()
        except Exception:
            return ""

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

        # v2 모드 요청 시 캡셔닝 모델 초기화 시도
        if request.get("use_v2") and request.get("caption_weights"):
            self._init_caption_model(request["caption_weights"], device)

        initialization_ms = (perf_counter() - started) * 1000 if initialized else 0
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
                imgsz=1280 if regions else 640,
            )

            for result in results:
                for box in result.boxes:
                    bbox = restore_region_box(
                        box.xyxy[0].cpu().tolist(), region, view.size, full_size
                    )
                    if bbox is not None:
                        func_desc = None
                        # v2 모드이고 캡셔닝 모델이 준비되었으면 아이콘 설명 생성
                        if request.get("use_v2") and self.caption_model is not None:
                            x, y, w, h = bbox
                            crop = image.crop((x, y, x + w, y + h))
                            func_desc = self._caption_crop(crop, device) or None

                        boxes.append(
                            {
                                "id": len(boxes),
                                "source": "omniparser",
                                "bbox": bbox,
                                "confidence": float(box.conf.item()),
                                "functional_description": func_desc,
                            }
                        )

        self.metrics = {
            "device": str(getattr(getattr(self.model, "predictor", None), "device", device)),
            "model_initialized": initialized,
            "model_init_ms": initialization_ms,
            "inference_ms": (perf_counter() - started) * 1000,
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