"""Conservative semantic input reuse; callers retain all policy and safety guards."""

from .semantic import (
    CacheIntegrityError,
    CacheMatch,
    CacheSession,
    SemanticCache,
    SemanticEntry,
    TargetDescriptor,
    describe_target,
    matching_targets,
    record_values,
    value_matches,
)

__all__ = [
    "CacheIntegrityError",
    "CacheMatch",
    "CacheSession",
    "SemanticCache",
    "SemanticEntry",
    "TargetDescriptor",
    "describe_target",
    "matching_targets",
    "record_values",
    "value_matches",
]
