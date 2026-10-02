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
from .visual_match import VisualMatchCache, VisualMatchCacheIntegrityError, visual_match_key

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
    "VisualMatchCache",
    "VisualMatchCacheIntegrityError",
    "visual_match_key",
]
