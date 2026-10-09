"""Typed callable contract for graph-level structured model requests."""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from sawon_runtime.visual import RenderedPage

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class ScreenModelCall(Protocol):
    """Return the concrete response schema requested by a screen workflow."""

    def __call__(
        self,
        prompt: str,
        schema: type[ResponseT],
        *,
        page: RenderedPage | None = None,
    ) -> ResponseT: ...
