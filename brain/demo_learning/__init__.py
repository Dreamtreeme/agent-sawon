"""Read-only observation recordings become proposed semantic input mappings."""

from .convert import (
    DemonstrationResult,
    convert_demonstration,
    install_demonstration,
    read_recording,
)

__all__ = [
    "DemonstrationResult",
    "convert_demonstration",
    "install_demonstration",
    "read_recording",
]
