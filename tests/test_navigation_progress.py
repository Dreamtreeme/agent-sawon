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
            observation.active_window = changed
            observation.windows = [changed]
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
            observation.elements = [
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
