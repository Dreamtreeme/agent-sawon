"""Plan visible field assignments once, fill the screen, then verify together."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import Field, model_validator
from sawon_interfaces import Observation

from brain.observe.evidence import matching_delivered_input
from brain.visual.scene import SceneObservation

from .execution import ActionPlan, GuardStop, PlanExecution, PlannedAction
from .schemas import Choice, Decision, DecisionResponse, FieldName, StrictModel

SCREEN_FILL_GUIDANCE = """
SCREEN INPUT MODE supports ordered current-screen inputs and an optional next action.
Plan ALL currently visible, directly editable inputs in fields, ordered for execution.
Each entry maps a CURRENT ref to a supplied record or opaque credential source;
never supply literal values or invented coordinates. Each target ref occurs once;
one source may supply several distinct fields when the task requires it.
The runtime fills every planned field without OCR/model calls between inputs,
then executes next_decision when it is a grounded CURRENT-screen action.
For login, put the remaining credential inputs in fields and the login button or
Enter command in next_decision. Credential delivery is not authentication.
When inputs are not followed by another action, next_decision is null.
On screens without input assignments, return fields=[] and the appropriate next_decision.
Only include fields whose input does not open a dialog, change tabs or navigate.
Sources are supplied input keys or available credential sources. Never use cells inside
a table/list/row as substitutes for similarly named record-level fields.
If remaining fields are absent from the current form, use observed tabs/navigation.
For fields requiring lookup/dialog/selection, first fill other directly editable
fields; handle that boundary in the next plan after verification.
Do not guess actions revealed only after navigation. Complete or stop may be selected
only without input assignments; they are results to review, not actions after writing.
"""


class ScreenField(StrictModel):
    source: FieldName
    ref: str = Field(min_length=1)


class ScreenFillPlan(StrictModel):
    purpose: str = Field(default="", max_length=160)
    fields: list[ScreenField] = Field(
        max_length=100,
        description="Ordered visible input assignments, including opaque credentials.",
    )
    next_decision: DecisionResponse | None = Field(
        description="Optional observed action after inputs; without inputs, an action or result."
    )

    @model_validator(mode="after")
    def one_path(self) -> ScreenFillPlan:
        if not self.fields and self.next_decision is None:
            raise ValueError("screen inputs or next decision required")
        if len({item.ref for item in self.fields}) != len(self.fields):
            raise ValueError("duplicate screen assignment")
        if self.fields and self.next_decision is not None:
            if self.next_decision.to_decision().action is None:
                raise ValueError("screen inputs require an executable next decision")
        return self


def prepare_screen_fill(scene: Observation, plan: ScreenFillPlan) -> ActionPlan:
    if not isinstance(scene, SceneObservation):
        raise GuardStop("screen_fill_requires_visual_scene")
    actions = [
        PlannedAction(
            Decision(
                screen_kind=scene.screen_kind,
                operation="act",
                intent="credential" if f.source.startswith("credential.") else "fill",
                subgoal=plan.purpose or "Fill visible screen",
                reason="Execute the current screen field plan",
                action=Choice(type="set_text", ref=f.ref, source=f.source),
            )
        )
        for f in plan.fields
    ]
    if plan.next_decision is not None:
        actions.append(PlannedAction(plan.next_decision.to_decision()))
    return ActionPlan(
        tuple(actions),
        "fields" if all(item.decision.intent == "fill" for item in actions) else "single",
        {
            "check": "screen_fill_plan",
            "assignments": [f.model_dump() for f in plan.fields],
        },
    )


@dataclass(frozen=True)
class ScreenFieldVerificationRequest:
    execution: PlanExecution
    observation: Observation
    visual_proposal: Any | None


@dataclass(frozen=True)
class ScreenFieldVerificationServices:
    input_surfaces: Callable[[str | None], set[str]]
    record_verified_field: Callable[[str, Any], bool]


@dataclass(frozen=True)
class ScreenFieldVerificationResult:
    payload: dict
    matched_refs: tuple[str, ...]
    failed_decision: Decision | None = None
    failure_reason: str | None = None


def verify_screen_fields(
    request: ScreenFieldVerificationRequest,
    services: ScreenFieldVerificationServices,
) -> ScreenFieldVerificationResult:
    """Evaluate one observed batch without reaching into mutable worker state."""

    execution, observation = request.execution, request.observation
    delivered_fields = [
        delivered for delivered in execution.delivered if delivered["choice"].source is not None
    ]
    matched, matched_refs, mismatched, mismatched_refs = [], [], [], []
    source_counts = Counter(item["choice"].source for item in delivered_fields)
    explicit_evidence = {}
    proposal = request.visual_proposal
    if proposal is not None and isinstance(observation, SceneObservation):
        prefix = "e" + str(observation.obs_id.int) + "0"
        explicit_evidence = {item.field: prefix + str(item.target) for item in proposal.evidence}
    by_ref = {element.ref: element for element in observation.elements}
    for delivered in delivered_fields:
        source = delivered["choice"].source
        bound = by_ref.get(explicit_evidence.get(source)) if source_counts[source] == 1 else None
        candidates = (
            [bound]
            if bound is not None
            else matching_delivered_input(
                observation,
                execution.before,
                delivered["choice"].ref,
                delivered["descriptor"],
                allowed_surfaces=services.input_surfaces(source),
            )
        )
        ok = (
            observation.active_window == execution.before.active_window
            and len(candidates) == 1
            and services.record_verified_field(source, candidates[0])
        )
        if ok:
            matched_refs.append(delivered["choice"].ref)
            matched.append(source)
        else:
            mismatched.append(source)
            mismatched_refs.append(delivered["choice"].ref)
    payload = {
        "check": "screen_fill_values",
        "ok": not mismatched,
        "matched": matched,
        "mismatched": mismatched,
        "reviewer": "runtime_value_check",
    }
    failed_decision = None
    if mismatched_refs:
        failed_decision = next(
            a.decision
            for a in execution.plan.actions
            if a.decision.action.ref == mismatched_refs[0]
        )
    return ScreenFieldVerificationResult(
        payload=payload,
        matched_refs=tuple(matched_refs),
        failed_decision=failed_decision,
        failure_reason="screen_fill_value_mismatch" if mismatched else None,
    )
