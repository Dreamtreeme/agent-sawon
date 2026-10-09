"""Provider-neutral runtime contracts shared by the worker and document pipeline."""

from .visual import DocumentKind, FrozenModel, OcrBlock, OcrEngine, RenderedPage

__all__ = ["DocumentKind", "FrozenModel", "OcrBlock", "OcrEngine", "RenderedPage"]
