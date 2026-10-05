"""Shared immutable test inputs whose consumers retain isolated runtime state."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def graph_cache_snapshot(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """Build one proven cold cache and expose only immutable serialized bytes."""
    from test_brain import make_record, run_brain, success_steps

    from brain.settings import BrainSettings, CacheSettings

    record = make_record()
    root = tmp_path_factory.mktemp("graph-cache-snapshot")
    path = root / "cache.json"
    settings = BrainSettings(cache=CacheSettings(enabled=True, path=path, matching_mode="rebind"))
    result, _, _, _ = run_brain(root / "cold", record, success_steps(record), settings=settings)
    assert result.status == "success"
    return path.read_bytes()
