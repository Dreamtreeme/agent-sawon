"""Relay replay regressions for navigation that acknowledges but changes no UI."""

from __future__ import annotations

from test_brain import FakeRelay
from test_brain import record as record


def test_synthetic_model_completion_retains_delivered_cache_candidate(
    tmp_path, record, monkeypatch
):
    """Synthetic 132 replay: real observation/verification, fake model and frame comparison."""
    from test_post_save_context import taught_task
    from test_screen_decision import named_response, setup_brain

    from brain.cache.pattern_matching import ScreenPatternCache, screen_pattern_key
    from brain.graph import engine
    from brain.graph.schemas import Choice, Decision
    from brain.graph.transitions import ActionTransition, ExpectedEffect

    fields = {
        source: {"source": source, "role": "edit", "bbox": [100, 15, 150, 30], "value": value}
        for source, value in FakeRelay(record).values.items()
    }
    response = named_response(
        screen_kind="editor",
        fields=fields,
        steps=[],
        operation="complete",
        procedure_step="task-action",
        completion_evidence=[{"condition_index": 1, "target": "hbl_no"}],
    )
    brain, model, *_ = setup_brain(
        tmp_path, record, [response, response], task_instruction=taught_task()
    )
    brain.settings = brain.settings.model_copy(
        update={"visual_model_only_review": True, "guard_mode": "observe_only"}
    )
    brain.save_issued = True
    observed = brain._observe()
    assert set(brain._observed_inputs()) == set(brain.values)
    choice = Choice(type="key", keys="pagedown", ref=observed.elements[0].ref)
    decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="navigate",
        action=choice,
        subgoal="Read remaining saved fields",
        reason="Expose lower detail fields",
    )
    brain.decision = decision
    brain.pending = {
        "before_observation": observed,
        "choice": choice,
        "decision": decision,
        "transition": ActionTransition(
            action_id="synthetic-last-scroll",
            before_obs_id=str(observed.obs_id),
            intent="navigate",
            expected_effect=ExpectedEffect.NAVIGATION_CHANGE,
        ),
    }
    # Only the visual comparison is fake: this test exercises business completion,
    # not the independently tested image-change algorithm.
    monkeypatch.setattr(
        engine,
        "compare_navigation",
        lambda *_: {"method": "synthetic", "changed": True, "reason": "synthetic_scroll"},
    )
    cache = ScreenPatternCache(tmp_path / "screen-cache.json", enabled=True)
    brain.visual_match_cache = cache
    cache_state = {"task": "synthetic-saved-review"}
    cache_key = screen_pattern_key(cache_state)
    candidate = {"synthetic_delivered_action": "pagedown"}
    brain.pending["visual_match_candidate"] = (cache_key, candidate)
    outcome = brain._verify_node()
    if outcome == "review":
        assert brain._select_node() == "review"
        outcome = brain._review_node()
    assert outcome == "finish"
    assert brain.result.status == "success", brain.result.reason
    assert set(brain._observed_inputs()) == set(brain.values)
    assert len(model.prompts) == 2, "reuse the observed completion without another model call"
    cache.finish(success=True)
    assert cache.lookup(cache_key) == (cache_key, candidate)
