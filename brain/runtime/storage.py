"""Compatibility exports for shared runtime storage."""

from sawon_runtime import storage as _storage
from sawon_runtime.storage import (
    CaseState,
    CaseStatus,
    InvalidTransitionError,
    LogIntegrityError,
    RunLog,
    StateStore,
    atomic_write_json,
)

os = _storage.os
time = _storage.time

__all__ = [
    "CaseState",
    "CaseStatus",
    "InvalidTransitionError",
    "LogIntegrityError",
    "RunLog",
    "StateStore",
    "atomic_write_json",
]
