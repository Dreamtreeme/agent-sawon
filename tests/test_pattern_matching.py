from brain.cache import ScreenPatternCache, screen_pattern_key


def test_visual_match_cache_promotes_only_a_successful_run(tmp_path):
    path = tmp_path / "visual.json"
    state = {"screen": {"markers": [1]}, "workflow": {"phase": "enter"}}
    key = screen_pattern_key(state)

    failed = ScreenPatternCache(path, enabled=True)
    failed.stage(key, {"procedure_step": "fill"})
    assert failed.finish(success=False) == 0
    assert not path.exists()

    successful = ScreenPatternCache(path, enabled=True)
    successful.stage(key, {"procedure_step": "fill"})
    assert successful.finish(success=True) == 1
    assert ScreenPatternCache(path, enabled=True).lookup(state) == (
        key,
        {"procedure_step": "fill"},
    )


def test_visual_match_cache_key_includes_workflow_state(tmp_path):
    cache = ScreenPatternCache(tmp_path / "visual.json", enabled=True)
    before = {"screen": {"markers": [1]}, "workflow": {"query_submitted": False}}
    after = {"screen": {"markers": [1]}, "workflow": {"query_submitted": True}}
    cache.stage(screen_pattern_key(before), {"procedure_step": "search"})
    assert cache.finish(success=True) == 1
    cache = ScreenPatternCache(tmp_path / "visual.json", enabled=True)
    assert cache.lookup(before) is not None
    assert cache.lookup(after) is None


def test_visual_match_cache_key_ignores_run_metadata_but_keeps_action_context():
    first = {
        "screen": {
            "obs_id": "observation-a",
            "observation_route": "scanning",
            "markers": [{"id": 7, "source": "ocr", "bbox": [1, 2, 3, 4], "confidence": 0.91}],
        },
        "notebook": {
            "inputs": {"hbl_no": "SEED-A", "shipper": "A"},
            "evidence_claims": [
                {"field": "hbl_no", "observation_id": "observation-a", "revision": 3}
            ],
            "recent_turns": [{"target_summary": "click:e-run-a"}],
            "failed_transitions": [{"action_key": "click:target:row"}],
            "workflow_state": {"save_issued": True},
        },
    }
    second = {
        "screen": {
            "obs_id": "observation-b",
            "observation_route": "memory_retrieval",
            "markers": [{"id": 7, "source": "ocr", "bbox": [1, 2, 3, 4], "confidence": 0.87}],
        },
        "notebook": {
            "inputs": {"hbl_no": "SEED-B", "shipper": "B"},
            "evidence_claims": [
                {"field": "hbl_no", "observation_id": "observation-b", "revision": 8}
            ],
            "recent_turns": [{"target_summary": "click:e-run-b"}],
            "failed_transitions": [{"action_key": "click:target:row"}],
            "workflow_state": {"save_issued": True},
        },
    }

    assert screen_pattern_key(first) == screen_pattern_key(second)
    second["notebook"]["failed_transitions"] = [{"action_key": "double_click:target:row"}]
    assert screen_pattern_key(first) != screen_pattern_key(second)


def test_visual_match_cache_key_does_not_depend_on_method_library_state():
    state = {
        "screen": {"markers": [{"id": 3, "bbox": [10, 20, 30, 40]}]},
        "task_contract_sha256": "a" * 64,
        "execution": {"phase": "task"},
    }
    with_learned_branch = {
        **state,
        "method_memory": {
            "methods": [{"id": "method-a", "groups": [{"id": "branch-1"}]}],
            "recent_executions": [{"group_id": "branch-1", "status": "delivered"}],
        },
    }

    assert screen_pattern_key(state) == screen_pattern_key(with_learned_branch)
