from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import Brain, Result


def __getattr__(name: str):
    if name in {"Brain", "Result"}:
        from .engine import Brain, Result

        return {"Brain": Brain, "Result": Result}[name]
    raise AttributeError(name)


__all__ = ["Brain", "Result"]
