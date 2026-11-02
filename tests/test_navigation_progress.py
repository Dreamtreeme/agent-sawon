"""Relay replay regressions for navigation that acknowledges but changes no UI."""

from __future__ import annotations

from test_brain import FakeRelay, element
from test_brain import record as record

from brain.graph.transitions import ExplorationAttempt, ExplorationLedger, exploration_action_key
from brain.observe.progress import navigation_state


class NavigationRelay(FakeRelay):
    """Expose synthetic tab actions with independent semantic and volatile changes."""

    def __init__(self, record, *, changes=(), volatile=False, alternate=False):
        super().__init__(record)
        self.changes = list(changes)
        self.volatile = volatile
        self.navigation_count = 0
        self.current_tab_ref = "e9"
        self.current_alternate_ref = "e13"
        self.active_change = "none"
        self.alternate = alternate
        self.alternate_count = 0

    def observe(self):
        observation = super().observe()
        if self.mode != "editor":
            return observation
        observation.elements.extend(
            [
                element("e9", "TabItem", name="Synthetic tab", parent="e10"),
                element("e10", "Group", name="Primary group", parent="e0"),
                element("e11", "Group", name="Secondary group", parent="e0"),
            ]
        )
        if self.alternate:
            observation.elements.append(
                element("e13", "Button", name="Alternative route", parent="e11")
            )
        field = next(item for item in observation.elements if item.role == "Edit")
        if self.active_change == "value":
            field.value = "Newly displayed contents"
        elif self.active_change == "name":
            field.name = "Newly displayed field"
        elif self.active_change == "label":
            field.label_hint = "Newly displayed label"
        elif self.active_change == "enabled":
            field.enabled = False
        elif self.active_change == "hierarchy":
            next(item for item in observation.elements if item.ref == "e9").parent_ref = "e11"
        elif self.active_change == "content":
            observation.elements.append(
                element("e12", "Text", name="New panel contents", parent="e11")
            )
        elif self.active_change == "window":
            changed = observation.active_window.model_copy(update={"handle": 2})
            observation.wire.active_window = changed
            observation.wire.windows = [changed]
        self.current_tab_ref = "e9"
        self.current_alternate_ref = "e13"
        if self.volatile:
            mapping = {
                item.ref: f"e{int(item.ref[1:]) + self.calls * 100}"
                for item in observation.elements
            }
            self.current_tab_ref = mapping["e9"]
            if self.alternate:
                self.current_alternate_ref = mapping["e13"]
            observation.elements[:] = [
                item.model_copy(
                    update={
                        "ref": mapping[item.ref],
                        "parent_ref": mapping.get(item.parent_ref),
                        "focused": self.calls % 2 == 0,
                        "bbox": (float(self.calls), 0.0, 100.0, 25.0),
                    }
                )
                for item in reversed(observation.elements)
            ]
            observation.screen_hash = f"{self.calls:040x}"
        return observation

    def act(self, action):
        if action.root.type == "click" and action.root.target.ref == self.current_tab_ref:
            if self.navigation_count < len(self.changes):
                self.active_change = self.changes[self.navigation_count]
            self.navigation_count += 1
        elif (
            self.alternate
            and action.root.type == "click"
            and action.root.target.ref == self.current_alternate_ref
        ):
            self.active_change = "content"
            self.alternate_count += 1
        return super().act(action)


def test_exploration_memory_ignores_key_focus_anchor_and_keeps_other_routes():
    ledger = ExplorationLedger()
    page_down = exploration_action_key("key", keys="pagedown", target_key="first-anchor")
    assert page_down == exploration_action_key(
        "key", keys="pagedown", target_key="different-anchor"
    )
    ledger.record(
        ExplorationAttempt(
            state_key="screen-a",
            goal_key="inputs:missing",
            action_key=page_down,
            action={"type": "key", "keys": "pagedown"},
            outcome="no_change",
        )
    )

    assert ledger.failed("screen-a", "inputs:missing", page_down)
    assert not ledger.failed(
        "screen-a",
        "inputs:missing",
        exploration_action_key("click", target_key="another-route"),
    )
    assert ledger.prompt_context("screen-a", "inputs:missing") == [
        {
            "action_key": "key:pagedown",
            "action": {"type": "key", "keys": "pagedown"},
            "outcome": "no_change",
        }
    ]


def test_navigation_state_tracks_each_same_window_semantic_change(record):
    relay = NavigationRelay(record)
    relay.mode = "editor"
    baseline = navigation_state(relay.observe())

    for change in ("value", "name", "label", "enabled", "hierarchy", "content"):
        relay.active_change = change
        observation = relay.observe()
        assert observation.active_window.handle == 1
        assert navigation_state(observation) != baseline, change


def test_new_window_with_same_controls_changes_navigation_state(record):
    relay = NavigationRelay(record)
    relay.mode = "editor"
    before = navigation_state(relay.observe())
    relay.active_change = "window"
    after = relay.observe()
    assert after.active_window.handle == 2
    assert navigation_state(after) != before


def test_visual_navigation_ignores_model_catalog_edits_on_identical_pixels():
    from test_visual_markers import fixture
    from test_visual_scene import wire

    from brain.observe.progress import navigation_state
    from brain.visual.markers import ground_meanings
    from brain.visual.scene import bind_scene

    blocks, _, markers, meaning = fixture()
    scene = bind_scene(wire(), blocks, ground_meanings(meaning, markers, blocks))
    scene = scene.model_copy(update={"capture_phash": 12345})
    reinterpreted = scene.model_copy(update={"elements": []})
    assert navigation_state(scene) == navigation_state(reinterpreted)
    assert navigation_state(scene) != navigation_state(
        scene.model_copy(update={"capture_phash": 54321})
    )


def test_synthetic_model_completion_survives_previous_scroll_cycle(tmp_path, record, monkeypatch):
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
    )
    brain, model, *_ = setup_brain(
        tmp_path, record, [response, response], task_instruction=taught_task()
    )
    brain.settings = brain.settings.model_copy(
        update={"visual_model_only_review": True, "guard_mode": "observe_only"}
    )
    brain.save_issued = brain.model_saved_detail_review = True
    brain.post_save_query_identity_verified = True
    observed = brain._observe()
    assert brain.post_save_verified == set(brain.values)
    destination = navigation_state(observed)
    source = "synthetic-upper-scroll-position"
    checkpoint = f"{brain.phase}:{brain.business_revision}"
    assert not brain.navigation_history.observe(source, destination, checkpoint)
    assert not brain.navigation_history.observe(destination, source, checkpoint)
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
        "source_navigation_state": source,
        "source_window_handle": 1,
        "exploration_action_key": "key:pagedown",
        "exploration_goal_key": brain._exploration_goal_key(),
        "exploration_action": {"type": "key", "keys": "pagedown"},
        "transition": ActionTransition(
            action_id="synthetic-last-scroll",
            before_obs_id=str(observed.obs_id),
            intent="navigate",
            expected_effect=ExpectedEffect.NAVIGATION_CHANGE,
        ),
    }
    # Seed one prior failure through the real counter, not by assuming its private key.
    brain._record_exploration_failure(brain.pending, "cycle")
    assert brain._fail("navigation_cycle") == "observation"
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
        outcome = brain._select_node()
    assert outcome == "finish"
    assert brain.result.status == "success", brain.result.reason
    assert brain.post_save_verified == set(brain.values)
    assert len(model.prompts) == 2, "reuse the observed completion without another model call"
    assert cache.finish(success=True) == 1, "terminal routing must retain the delivered candidate"
    assert cache.lookup(cache_key) == (cache_key, candidate)


def test_synthetic_scroll_failure_budget_belongs_to_screen_and_goal(tmp_path, record):
    """Synthetic failures: same key on another task scope or screen is a fresh attempt."""
    from test_screen_decision import setup_brain

    brain, model, *_ = setup_brain(tmp_path, record, [])
    brain.observation = FakeRelay(record).observe()
    pending = {
        "source_navigation_state": "screen-a",
        "source_window_handle": 1,
        "exploration_action_key": "key:pagedown",
        "exploration_goal_key": "inputs:packages.count",
        "exploration_action": {"type": "key", "keys": "pagedown"},
    }
    for state, goal in [
        ("screen-a", "inputs:packages.count"),
        ("screen-a", "post_save_detail:packages.count"),
        ("screen-b", "post_save_detail:packages.count"),
    ]:
        pending.update(source_navigation_state=state, exploration_goal_key=goal)
        brain._record_exploration_failure(pending, "cycle")
        assert brain._fail("navigation_cycle") == "observation", (state, goal)
    brain._record_exploration_failure(pending, "cycle")
    assert brain._fail("navigation_cycle") == "finish"
    assert brain.result.status == "stopped"
    assert brain.result.reason.startswith("repeated_subgoal_failure:")
    assert model.prompts == []
