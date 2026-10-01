"""Optional pixel-only noise removal; original documents and coordinates stay intact."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .ocr import OcrEngine
from .ocr_validation import validate_ocr_blocks, validate_ocr_limits
from .schemas import OcrBlock, OcrInput, PipelineSettings, RenderedPage


def prepare_ocr_input(
    page: RenderedPage, settings: PipelineSettings, *, case_dir: Path
) -> tuple[RenderedPage, OcrInput]:
    """Keep a separate, auditable OCR image without using text or another document.

    Median filtering can remove isolated fax noise, but can also remove thin
    strokes. It is opt-in and never changes extracted values or validation rules.
    Vision still receives the original rendered page. No rotation or rescaling
    is done, so OCR boxes continue to address that original page directly.
    """
    path = page.image_path
    kernel = settings.ocr_median_kernel
    if kernel:
        from PIL import Image, ImageFilter

        destination = path.parent / "ocr-input" / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as source:
            if source.size != (page.width, page.height):
                raise ValueError("ocr_input_dimensions_mismatch")
            filtered = source.convert("RGB").filter(ImageFilter.MedianFilter(kernel))
            with destination.open("xb") as stream:
                filtered.save(stream, format="PNG")
        path = destination
    metadata = OcrInput(
        image=path.relative_to(case_dir).as_posix(),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        median_kernel=kernel,
        original_image=(
            page.image_path.relative_to(case_dir).as_posix()
            if settings.ocr_include_original
            else None
        ),
        original_sha256=(
            hashlib.sha256(page.image_path.read_bytes()).hexdigest()
            if settings.ocr_include_original
            else None
        ),
    )
    return page.model_copy(update={"image_path": path}), metadata


def read_ocr_views(
    engine: OcrEngine,
    original: RenderedPage,
    processed: RenderedPage,
    settings: PipelineSettings,
) -> list[OcrBlock]:
    """Keep both readings as distinct evidence; never choose by another document.

    The extractor sees variants and selects evidence using the original image.
    IDs are local to this combined observation, so a filtered ID cannot silently
    resolve to an original block with different text.
    """
    blocks: list[OcrBlock] = []
    views = [(processed, "median" if settings.ocr_median_kernel else "original")]
    if settings.ocr_include_original:
        views.insert(0, (original, "original"))
    for page, variant in views:
        observed = engine.read(page)
        validate_ocr_blocks(observed, width=original.width, height=original.height)
        for block in observed:
            blocks.append(block.model_copy(update={"id": len(blocks), "input_variant": variant}))
        validate_ocr_limits(
            blocks,
            max_blocks=settings.max_ocr_blocks,
            max_characters=settings.max_ocr_characters,
        )
    return blocks
