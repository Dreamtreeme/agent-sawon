"""Relay replay regressions for navigation that acknowledges but changes no UI."""

from __future__ import annotations

import json

import pytest
from test_brain import FakeRelay, decision, element, run_brain, success_steps
from test_brain import record as record


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


def navigate(intent="navigate", *, claim="Open the next panel"):
    def choose_current_tab(prompt):
        ui = json.loads(prompt.split("\nUI_TEXT=", 1)[1])
        tab = next(item for item in ui["elements"] if item["role"] == "TabItem")
        return {
            **decision(kind="editor", intent=intent, action={"type": "click", "ref": tab["ref"]}),
            "subgoal": claim,
        }

    return choose_current_tab


def no_progress_failures(brain):
    return [
        event.payload
        for event in brain.log.read_all()
        if event.kind == "verify" and event.payload.get("reason") == "navigation_no_progress"
    ]


def task_state(prompt):
    return json.loads(prompt.split("\nTRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0])


def alternate_navigation(prompt):
    ui = json.loads(prompt.split("\nUI_TEXT=", 1)[1])
    alternative = next(item for item in ui["elements"] if item["name"] == "Alternative route")
    return decision(
        kind="editor", intent="select_tab", action={"type": "click", "ref": alternative["ref"]}
    )


@pytest.mark.parametrize("intent", ["navigate", "select_tab"])
def test_unchanged_navigation_blocks_second_click_and_stops_after_two_failures(
    tmp_path, record, intent
):
    relay = NavigationRelay(record)
    claims = ["Open the next panel", "Now try a different subgoal name"]
    result, brain, _, model = run_brain(
        tmp_path,
        record,
        [
            *success_steps(record)[:4],
            *(navigate(intent, claim=claim) for claim in claims),
            decision("stop", intent="stop"),
        ],
        relay=relay,
    )
    assert result.status == "stopped"
    assert result.reason == "repeated_subgoal_failure:enter:" + intent
    assert result.steps == 6
    assert len(model.prompts) == 7  # Planning and six bounded decisions.
    assert relay.navigation_count == 1
    assert len(no_progress_failures(brain)) == 2
    assert not any(item.get("ok") and item.get("subgoal") in claims for item in brain.feedback)
    returned = [
        event.payload
        for event in brain.log.read_all()
        if event.kind == "act" and event.payload.get("stage") == "returned"
    ]
    assert len(returned) == 4  # Query input, search, editor navigation, first failed tab.
    assert all(item["ok"] for item in returned)


def test_ref_focus_geometry_hash_and_model_prose_churn_do_not_count_as_progress(tmp_path, record):
    relay = NavigationRelay(record, volatile=True)
    refs = []

    def retry_with_current_ref(prompt):
        ui = json.loads(prompt.split("\nUI_TEXT=", 1)[1])
        current_tab = next(item for item in ui["elements"] if item["role"] == "TabItem")
        failed = task_state(prompt)["unproductive_navigation_targets"]
        assert len(failed) == 1
        assert failed[0]["ref"] == current_tab["ref"]
        assert failed[0]["role"] == "TabItem"
        assert failed[0]["name"] == "Synthetic tab"
        assert failed[0]["label"] is None
        assert {"role": "Group", "name": "Primary group", "label": None} in failed[0]["context"]
        refs.extend([relay.actions[-1].root.target.ref, current_tab["ref"]])
        assert refs[0] != refs[1]
        return navigate(claim="Panel two is now open")(prompt)

    result, brain, _, _ = run_brain(
        tmp_path,
        record,
        [
            *success_steps(record)[:4],
            navigate(claim="Panel one is now open"),
            retry_with_current_ref,
            decision("stop", intent="stop"),
        ],
        relay=relay,
    )
    assert result.reason == "repeated_subgoal_failure:enter:navigate"
    assert len(no_progress_failures(brain)) == 2
    assert relay.navigation_count == 1
    assert len(refs) == 2
    assert refs[0] != refs[1]
    assert refs[1] not in [action.root.target.ref for action in relay.actions]


def test_alternating_navigation_intents_cannot_evade_the_phase_cap(tmp_path, record):
    relay = NavigationRelay(record)
    result, brain, _, _ = run_brain(
        tmp_path,
        record,
        [
            *success_steps(record)[:4],
            navigate("navigate"),
            navigate("select_tab"),
            decision("stop", intent="stop"),
        ],
        relay=relay,
    )
    assert result.reason == "repeated_phase_failure:enter"
    assert relay.navigation_count == 1
    assert [item["phase_failures_without_progress"] for item in no_progress_failures(brain)] == [
        1,
        2,
    ]


@pytest.mark.parametrize("change", ["value", "name", "label", "enabled", "hierarchy", "content"])
def test_semantic_change_in_same_window_accepts_navigation(tmp_path, record, change):
    relay = NavigationRelay(record, changes=[change])
    result, brain, _, _ = run_brain(
        tmp_path,
        record,
        [*success_steps(record)[:4], navigate(), decision("stop", intent="stop")],
        relay=relay,
    )
    assert result.reason == "model_requested_stop"
    assert brain.observation.active_window.handle == 1
    assert not no_progress_failures(brain)
    assert any(
        item.get("ok") and item.get("subgoal") == "Open the next panel" for item in brain.feedback
    )


def test_new_window_with_same_controls_counts_as_navigation_progress(tmp_path, record):
    relay = NavigationRelay(record, changes=["window"])
    result, brain, _, _ = run_brain(
        tmp_path,
        record,
        [*success_steps(record)[:4], navigate(), decision("stop", intent="stop")],
        relay=relay,
    )
    assert result.reason == "model_requested_stop"
    assert brain.observation.active_window.handle == 2
    assert not no_progress_failures(brain)


def test_real_same_window_progress_resets_phase_failures(tmp_path, record):
    relay = NavigationRelay(record, changes=["none", "content"], alternate=True)
    result, brain, _, _ = run_brain(
        tmp_path,
        record,
        [
            *success_steps(record)[:4],
            navigate("navigate"),
            alternate_navigation,
            navigate("select_tab"),
            decision("stop", intent="stop"),
        ],
        relay=relay,
    )
    assert result.reason == "model_requested_stop"
    assert relay.navigation_count == 2
    assert relay.alternate_count == 1
    failures = no_progress_failures(brain)
    assert len(failures) == 2
    assert [item["phase_failures_without_progress"] for item in failures] == [1, 1]


def test_failed_target_prompt_allows_recovery_via_another_observed_control(tmp_path, record):
    relay = NavigationRelay(record, alternate=True, volatile=True)

    def choose_alternative(prompt):
        failed = task_state(prompt)["unproductive_navigation_targets"]
        assert len(failed) == 1
        assert failed[0]["name"] == "Synthetic tab"
        alternative = alternate_navigation(prompt)
        assert alternative["action"]["ref"] != failed[0]["ref"]
        return alternative

    result, brain, _, _ = run_brain(
        tmp_path,
        record,
        [
            *success_steps(record)[:4],
            navigate(),
            choose_alternative,
            decision("stop", intent="stop"),
        ],
        relay=relay,
    )
    assert result.reason == "model_requested_stop"
    assert relay.navigation_count == relay.alternate_count == 1
    assert len(no_progress_failures(brain)) == 1
    assert brain.phase_failures["enter"] == 0


def test_failed_target_is_allowed_again_after_a_real_state_change(tmp_path, record):
    relay = NavigationRelay(record, changes=["none", "value"], alternate=True)

    def retry_after_change(prompt):
        assert task_state(prompt)["unproductive_navigation_targets"] == []
        return navigate()(prompt)

    result, brain, _, _ = run_brain(
        tmp_path,
        record,
        [
            *success_steps(record)[:4],
            navigate(),
            alternate_navigation,
            retry_after_change,
            decision("stop", intent="stop"),
        ],
        relay=relay,
    )
    assert result.reason == "model_requested_stop"
    assert relay.navigation_count == 2
    assert relay.alternate_count == 1
    assert len(no_progress_failures(brain)) == 1
    assert brain.phase_failures["enter"] == 0
