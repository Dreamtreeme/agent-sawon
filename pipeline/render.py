"""Rasterize original documents; never use synthetic HTML or truth sidecars."""

from __future__ import annotations

import math
from pathlib import Path

from .schemas import DocumentInput, PipelineSettings, RenderedPage


class PipelineDependencyError(RuntimeError):
    """An explicitly requested pipeline component is unavailable."""


def render_document(
    document: DocumentInput, output_dir: Path, settings: PipelineSettings
) -> list[RenderedPage]:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise PipelineDependencyError(
            "Install core[pipeline] for Pillow document rendering"
        ) from exc
    path = document.path
    if not path.is_file() or path.stat().st_size > settings.max_document_bytes:
        raise ValueError("document_missing_or_too_large")
    suffix = path.suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}:
        raise ValueError("unsupported_document_type")
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[RenderedPage] = []

    def save(image, page: int) -> None:
        width, height = image.size
        if width * height > settings.max_pixels:
            factor = math.sqrt(settings.max_pixels / (width * height))
            image = image.resize((max(1, int(width * factor)), max(1, int(height * factor))))
        destination = output_dir / f"{path.stem}-page-{page:04d}.png"
        image.convert("RGB").save(destination)
        rendered.append(
            RenderedPage(
                document=f"originals/{path.name}",
                kind=document.kind,
                page=page,
                image_path=destination,
                width=image.width,
                height=image.height,
            )
        )

    if suffix == ".pdf":
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise PipelineDependencyError(
                "Install core[pipeline] for pypdfium2 PDF rendering"
            ) from exc
        pdf = pdfium.PdfDocument(str(path))
        try:
            if len(pdf) > settings.max_pages or not len(pdf):
                raise ValueError("document_page_limit")
            for index in range(len(pdf)):
                page = pdf[index]
                try:
                    width, height = page.get_size()
                    scale = min(
                        settings.render_dpi / 72,
                        math.sqrt(settings.max_pixels / (width * height)),
                    )
                    bitmap = page.render(scale=scale)
                    try:
                        save(bitmap.to_pil(), index + 1)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        finally:
            pdf.close()
    else:
        with Image.open(path) as image:
            frame_count = getattr(image, "n_frames", 1)
            if frame_count > settings.max_pages:
                raise ValueError("document_page_limit")
            for frame in range(frame_count):
                image.seek(frame)
                save(ImageOps.exif_transpose(image), frame + 1)
    return rendered
