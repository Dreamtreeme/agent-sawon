from brain.cache import VisualMatchCache, visual_match_key


def test_visual_match_cache_promotes_only_a_successful_run(tmp_path):
    path = tmp_path / "visual.json"
    state = {"screen": {"markers": [1]}, "workflow": {"phase": "enter"}}
    key = visual_match_key(state)

    failed = VisualMatchCache(path, enabled=True)
    failed.stage(key, {"procedure_step": "fill"})
    assert failed.finish(success=False) == 0
    assert not path.exists()

    successful = VisualMatchCache(path, enabled=True)
    successful.stage(key, {"procedure_step": "fill"})
    assert successful.finish(success=True) == 1
    assert VisualMatchCache(path, enabled=True).lookup(state) == (
        key,
        {"procedure_step": "fill"},
    )


def test_visual_match_cache_key_includes_workflow_state(tmp_path):
    cache = VisualMatchCache(tmp_path / "visual.json", enabled=True)
    before = {"screen": {"markers": [1]}, "workflow": {"query_submitted": False}}
    after = {"screen": {"markers": [1]}, "workflow": {"query_submitted": True}}
    cache.stage(visual_match_key(before), {"procedure_step": "search"})
    assert cache.finish(success=True) == 1
    cache = VisualMatchCache(tmp_path / "visual.json", enabled=True)
    assert cache.lookup(before) is not None
    assert cache.lookup(after) is None
