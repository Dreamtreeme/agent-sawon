"""Compatibility exports for the shared persistent worker runtime."""

from sawon_runtime.persistent_worker import PersistentWorker, WorkerError

__all__ = ["PersistentWorker", "WorkerError"]
