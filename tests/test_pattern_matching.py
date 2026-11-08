import hashlib
import json

from brain.cache import ScreenPatternCache, screen_pattern_key


def test_window_handles_are_session_local_but_window_semantics_and_result_values_are_not():
    first = {
        "screen": {
            "active_window": {"handle": 1, "title": "Mail", "process": "browser.exe"},
            "windows": [{"handle": 1, "title": "Mail", "process": "browser.exe"}],
        },
        "notebook": {"collected_result": {"values": {"handle": "ORDER-A"}}},
    }
    second = json.loads(json.dumps(first))
    second["screen"]["active_window"]["handle"] = 99
    second["screen"]["windows"][0]["handle"] = 99
    assert screen_pattern_key(first) == screen_pattern_key(second)
    second["screen"]["active_window"]["title"] = "Chat"
    assert screen_pattern_key(first) != screen_pattern_key(second)
    second["screen"]["active_window"]["title"] = "Mail"
    second["notebook"]["collected_result"]["values"]["handle"] = "ORDER-B"
    assert screen_pattern_key(first) != screen_pattern_key(second)


def test_visual_match_cache_promotes_only_a_successful_run(tmp_path):
    path = tmp_path / "visual.json"
    state = {"screen": {"markers": [1]}, "workflow": {"phase": "task"}}
    key = screen_pattern_key(state)
    # Synthetic pre-source-contract key: preserve its file but never parse the old response.
    old_key = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    legacy = ScreenPatternCache(tmp_path / "previous.json", enabled=True)
    legacy.stage(old_key, {"assignments": {"Reference": "hbl_no"}})
    previous_scroll_key = hashlib.sha256(
        b"screen-field-source-v1\0"
        + json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    legacy.stage(previous_scroll_key, {"mapping": {"steps": [{"action": "key", "keys": "pageup"}]}})
    assert legacy.finish(success=True) == 2
    before = legacy.path.read_bytes()
    assert legacy.lookup(key) is None
    assert legacy.path.read_bytes() == before

    failed = ScreenPatternCache(path, enabled=True)
    failed.stage(key, {"procedure_step": "fill"})
    assert failed.finish(success=False) == 0
    assert not path.exists()

    successful = ScreenPatternCache(path, enabled=True)
    successful.stage(key, {"procedure_step": "fill"})
    assert successful.finish(success=True) == 1
    assert ScreenPatternCache(path, enabled=True).lookup(key) == (
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
    assert cache.lookup(screen_pattern_key(before)) is not None
    assert cache.lookup(screen_pattern_key(after)) is None


def test_visual_match_cache_key_ignores_run_metadata_but_keeps_action_context():
    first = {
        "screen": {
            "obs_id": "observation-a",
            "observation_route": "scanning",
            "markers": [{"id": 7, "source": "ocr", "bbox": [1, 2, 3, 4], "confidence": 0.91}],
        },
        "notebook": {
            "inputs": {"hbl_no": "SEED-A", "shipper": "A"},
            "verified_inputs": ["hbl_no"],
            "recent_turns": [{"target_summary": "click:e-run-a"}],
            "workflow_state": {"save_issued": True},
        },
        "execution": {"last_delivered_actions": [{"intent": "navigate", "action": "click"}]},
    }
    second = {
        "screen": {
            "obs_id": "observation-b",
            "observation_route": "memory_retrieval",
            "markers": [{"id": 7, "source": "ocr", "bbox": [1, 2, 3, 4], "confidence": 0.87}],
        },
        "notebook": {
            "inputs": {"hbl_no": "SEED-B", "shipper": "B"},
            "verified_inputs": ["hbl_no"],
            "recent_turns": [{"target_summary": "click:e-run-b"}],
            "workflow_state": {"save_issued": True},
        },
        "execution": {"last_delivered_actions": [{"intent": "navigate", "action": "click"}]},
    }

    assert screen_pattern_key(first) == screen_pattern_key(second)
    second["execution"]["last_delivered_actions"][0]["action"] = "double_click"
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
