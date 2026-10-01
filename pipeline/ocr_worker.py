"""Standalone PaddleOCR v3 bridge, including for a separate Python environment.

Only stdlib and paddleocr are imported here. Result files contain private OCR
text and live in the case directory. No provider API is called by this worker.
"""

from __future__ import annotations

import json
import os
import site
import sys
from pathlib import Path
from time import perf_counter

_DLL_HANDLES = []


def configure_gpu_runtime() -> None:
    """Register the isolated Windows Paddle runtime's CUDA/cuDNN libraries."""
    if sys.platform != "win32" or _DLL_HANDLES:
        return
    candidates = [
        Path(value)
        for name in ("PADDLE_CUDA_BIN_DIR", "PADDLE_CUDNN_BIN_DIR")
        if (value := os.environ.get(name))
    ]
    for folder in site.getsitepackages():
        nvidia = Path(folder) / "nvidia"
        candidates.extend((nvidia / "cu13" / "bin" / "x86_64", nvidia / "cudnn" / "bin"))
    for path in dict.fromkeys(candidates):
        if path.is_dir():
            os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")
            _DLL_HANDLES.append(os.add_dll_directory(str(path)))


def create_engine(
    language: str,
    device: str,
    enable_mkldnn: bool = False,
    cpu_threads: int = 4,
    ocr_version: str = "PP-OCRv5",
    detection_model: str | None = "PP-OCRv5_mobile_det",
    recognition_model: str | None = None,
):
    if device.startswith("gpu"):
        configure_gpu_runtime()
    from paddleocr import PaddleOCR

    selection = {"lang": language, "ocr_version": ocr_version}
    if detection_model or recognition_model:
        if recognition_model is None:
            mobile_recognizers = {
                "en": "en_PP-OCRv5_mobile_rec",
                "korean": "korean_PP-OCRv5_mobile_rec",
            }
            recognition_model = mobile_recognizers.get(language)
            if recognition_model is None or ocr_version != "PP-OCRv5":
                raise ValueError(
                    "Configure an explicit recognition model for this language/version"
                )
        selection = {
            "text_detection_model_name": detection_model,
            "text_recognition_model_name": recognition_model,
        }
    return PaddleOCR(
        **selection,
        device=device,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        enable_mkldnn=enable_mkldnn,
        cpu_threads=cpu_threads,
    )


def recognize(engine, image: str) -> list[dict]:
    blocks = []
    for result in engine.predict(image):
        raw = result.json
        raw = json.loads(raw) if isinstance(raw, str) else raw
        payload = raw.get("res", raw)
        texts = payload.get("rec_texts", [])
        scores = payload.get("rec_scores", [])
        boxes = payload.get("rec_boxes", [])
        if len(texts) != len(scores) or len(texts) != len(boxes):
            raise ValueError("paddle_result_shape_mismatch")
        for text, score, box in zip(texts, scores, boxes, strict=True):
            if not str(text).strip():
                continue
            left, top, right, bottom = (int(value) for value in box)
            blocks.append(
                {
                    "id": len(blocks),
                    "text": str(text),
                    "confidence": float(score),
                    "bbox": [left, top, right - left, bottom - top],
                }
            )
    return blocks


class OcrSession:
    def __init__(self):
        self.engine = None
        self.key = None
        self.metrics = {}

    def __call__(self, request):
        key = tuple(
            request[k]
            for k in (
                "language",
                "device",
                "enable_mkldnn",
                "cpu_threads",
                "ocr_version",
                "detection_model",
                "recognition_model",
            )
        )
        initialized = self.engine is None or key != self.key
        started = perf_counter()
        if initialized:
            self.engine = create_engine(*key)
            self.key = key
        initialization_ms = (perf_counter() - started) * 1000 if initialized else 0
        started = perf_counter()
        blocks = recognize(self.engine, request["image"])
        self.metrics = {
            "device": request["device"],
            "model_initialized": initialized,
            "model_init_ms": initialization_ms,
            "inference_ms": (perf_counter() - started) * 1000,
        }
        return {"blocks": blocks}


def main() -> None:
    if sys.argv[1:] != ["--serve"]:
        raise SystemExit("Usage: ocr_worker.py --serve")
    from worker_server import serve

    serve(OcrSession())


if __name__ == "__main__":
    main()
