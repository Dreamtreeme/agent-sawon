"""All model requests enter through a measured ModelClient implementation."""

from typing import TYPE_CHECKING

from models.base import ModelClient, ModelResult, TokenUsage, UsageUnavailableError
from models.factory import create_model_client
from models.replay import ReplayClient, ReplayEntry, ReplayExhaustedError, ReplayMismatchError

__all__ = [
    "GeminiClient",
    "ModelClient",
    "ModelResult",
    "ReplayClient",
    "ReplayEntry",
    "ReplayExhaustedError",
    "ReplayMismatchError",
    "TokenUsage",
    "UsageUnavailableError",
    "create_model_client",
]


if TYPE_CHECKING:
    from models.gemini import GeminiClient


def __getattr__(name: str):
    if name == "GeminiClient":
        from models.gemini import GeminiClient

        return GeminiClient
    raise AttributeError(name)
