"""Plan visible field assignments once, fill the screen, then verify together."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field, model_validator

from brain.observe import describe
from brain.visual.scene import SceneObservation

from .schemas import Choice, Decision, DecisionResponse, FieldName, StrictModel

if TYPE_CHECKING:
    from .engine import Brain


SCREEN_FILL_GUIDANCE = """
SCREEN INPUT MODE overrides the single-action instruction for field entry.
Only editor screens may have field assignments; on query/menu/dialog screens,
return an empty fields list and the appropriate next_decision instead.
Plan ALL currently visible, directly editable remaining record fields in one fields
list, ordered for execution. Each entry maps a CURRENT ref to a record source;
never supply literal values or invented coordinates. Each ref/source occurs once.
The runtime fills every planned field without OCR/model calls between inputs,
then moves focus once and rereads the whole screen to verify the assignments.
Only include fields whose input does not open a dialog, change tabs or navigate.
For fields requiring lookup/dialog/selection, first fill other directly editable
fields; handle that boundary in the next plan after verification.
When fields is nonempty, next_decision must be null. When no remaining visible
direct input is available, fields is empty and next_decision contains the ordinary
allowed decision (navigation/tab/lookup/save/stop etc.). Do not fall back to a
single text fill: put all visible direct inputs in fields instead.
Save is a separate decision after the runtime has verified all required fields.
"""


class ScreenField(StrictModel):
    source: FieldName
    ref: str = Field(min_length=1)


class ScreenFillPlan(StrictModel):
    fields: list[ScreenField] = Field(max_length=11)
    next_decision: DecisionResponse | None

    @model_validator(mode="after")
    def one_path(self) -> ScreenFillPlan:
        if bool(self.fields) == (self.next_decision is not None):
            raise ValueError("choose screen fields or next decision")
        for key in ("source", "ref"):
            values = [getattr(item, key) for item in self.fields]
            if len(set(values)) != len(values):
                raise ValueError("duplicate screen assignment")
        if self.next_decision is not None:
            decision = self.next_decision.to_decision()
            if decision.intent == "fill" and decision.action is not None:
                raise ValueError("put direct field inputs in the screen plan")
        return self


def execute_screen_fill(brain: Brain, plan: ScreenFillPlan) -> dict:
    from .engine import GuardStop, field_matches

    scene = brain.observation
    if (
        not isinstance(scene, SceneObservation)
        or brain.phase != "enter"
        or not brain.duplicate_clear
    ):
        raise GuardStop("screen_fill_requires_editor_phase")
    if brain.steps + len(plan.fields) > brain.settings.max_steps:
        raise GuardStop("max_steps")
    by_ref = {item.ref: item for item in scene.elements}
    decisions = []
    descriptors = {}
    # Validate the entire plan before sending its first input.
    for field in plan.fields:
        decision = Decision(
            screen_kind="editor",
            operation="act",
            intent="fill",
            subgoal="Fill visible screen",
            reason="Execute the current screen field plan",
            action=Choice(type="set_text", ref=field.ref, source=field.source),
        )
        brain._guard_action(decision)
        descriptors[field.source] = describe(by_ref[field.ref], scene.elements)
        decisions.append(decision)
    brain.log.append(
        "select",
        payload={
            "check": "screen_fill_plan",
            "obs_id": str(scene.obs_id),
            "assignments": [field.model_dump() for field in plan.fields],
        },
    )
    for index, decision in enumerate(decisions):
        if index:
            brain.steps += 1
        brain.decision = decision
        # The first input requires unchanged content. Subsequent inputs use the
        # plan's positions with fresh relay captures; our edits have changed pixels.
        result = brain._act_node({}, screen_batch=index > 0)
        brain.pending = None
        if result["next"] != "verify":
            return result

    # Commit the last edit and remove its caret before the single visual reread.
    last = decisions[-1]
    brain.decision = last.model_copy(
        update={
            "intent": "navigate",
            "action": Choice(type="key", ref=last.action.ref, keys="tab"),
            "reason": "Finish screen entry before joint value verification",
        }
    )
    brain._guard_action(brain.decision)
    brain.steps += 1
    result = brain._act_node({}, screen_batch=True)
    brain.pending = None
    if result["next"] != "verify":
        return result
    observation = brain._observe()
    matched, mismatched = [], []
    for field in plan.fields:
        candidates = [
            item
            for item in observation.elements
            if describe(item, observation.elements) == descriptors[field.source]
        ]
        ok = (
            observation.active_window == scene.active_window
            and len(candidates) == 1
            and field_matches(field.source, candidates[0].value, brain.values)
        )
        if ok:
            brain.verified[field.source] = descriptors[field.source]
            brain._record_progress("field:" + field.source)
            matched.append(field.source)
        else:
            brain.verified.pop(field.source, None)
            mismatched.append(field.source)
    brain.log.append(
        "verify",
        payload={
            "check": "screen_fill_values",
            "ok": not mismatched,
            "matched": matched,
            "mismatched": mismatched,
            "obs_id": str(observation.obs_id),
        },
    )
    brain.feedback.append(
        {
            "ok": not mismatched,
            "check": "screen_fill_values",
            "matched": matched,
            "mismatched": mismatched,
        }
    )
    if mismatched:
        brain.decision = next(d for d in decisions if d.action.source == mismatched[0])
        outcome = brain._fail("screen_fill_value_mismatch")
        if outcome["next"] == "finish":
            return outcome
    # Verification already produced the next scene; don't run full perception again.
    return {"next": "review"}
