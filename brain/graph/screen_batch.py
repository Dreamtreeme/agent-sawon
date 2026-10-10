"""Plan visible field assignments once, fill the screen, then verify together."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import Field, model_validator
from sawon_interfaces import Observation

from brain.observe.evidence import matching_delivered_input
from brain.visual.scene import SceneObservation

from .execution import ActionPlan, GuardStop, PlanExecution, PlannedAction
from .policy import DecisionPolicy
from .schemas import Choice, Decision, DecisionResponse, FieldName, StrictModel

SCREEN_FILL_GUIDANCE = """
SCREEN INPUT MODE overrides the single-action instruction for field entry.
Any non-login business screen may have field assignments when it visibly contains
directly editable inputs. On screens without such inputs, return an empty fields list
and the appropriate next_decision instead.
Plan ALL currently visible, directly editable remaining record fields in one fields
list, ordered for execution. Each entry maps a CURRENT ref to a record source;
never supply literal values or invented coordinates. Each target ref occurs once;
one source may supply several distinct fields when the task requires it.
The runtime fills every planned field without OCR/model calls between inputs,
then moves focus once and rereads the whole screen to verify the assignments.
Only include fields whose input does not open a dialog, change tabs or navigate.
Sources are exactly the keys in the supplied task input record. Never use cells inside
a table/list/row as substitutes for similarly named record-level fields.
If remaining fields are absent from the current form, use observed tabs/navigation.
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
    purpose: str = Field(default="", max_length=160)
    fields: list[ScreenField] = Field(max_length=100)
    next_decision: DecisionResponse | None

    @model_validator(mode="after")
    def one_path(self) -> ScreenFillPlan:
        if bool(self.fields) == (self.next_decision is not None):
            raise ValueError("choose screen fields or next decision")
        if len({item.ref for item in self.fields}) != len(self.fields):
            raise ValueError("duplicate screen assignment")
        if self.next_decision is not None:
            decision = self.next_decision.to_decision()
            if decision.intent == "fill" and decision.action is not None:
                raise ValueError("put direct field inputs in the screen plan")
        return self


LOGIN_GUIDANCE = """
LOGIN SCREEN MODE overrides the single-action credential instructions.
On a login screen, plan ALL remaining available credential inputs and the login
button together: fields contains source/ref mappings and login_ref is the current
login command. The runtime supplies opaque credential values, executes the inputs
then clicks login without intermediate OCR/model calls, and observes the result once.
Do not include credential values in your answer. Input delivery is not authentication.
If no login form is visible, fields=[], login_ref=null, and next_decision contains
the ordinary allowed query/navigation/stop decision. Do not return a single
credential or login action in next_decision; those belong to the login screen plan.
When fields and login_ref are supplied, next_decision=null.
"""


class CredentialField(StrictModel):
    source: Literal["credential.username", "credential.password"]
    ref: str = Field(min_length=1)


class LoginScreenPlan(StrictModel):
    fields: list[CredentialField] = Field(max_length=2)
    login_ref: str | None
    next_decision: DecisionResponse | None

    @model_validator(mode="after")
    def one_path(self) -> LoginScreenPlan:
        if self.fields:
            if not self.login_ref or self.next_decision is not None:
                raise ValueError("credential plan requires login command")
            for key in ("source", "ref"):
                if len({getattr(f, key) for f in self.fields}) != len(self.fields):
                    raise ValueError("duplicate credential assignment")
            if self.login_ref in {f.ref for f in self.fields}:
                raise ValueError("login command cannot be an input")
        elif self.login_ref is not None or self.next_decision is None:
            raise ValueError("choose login plan or ordinary next decision")
        elif self.next_decision.to_decision().intent in {"credential", "login"}:
            raise ValueError("credentials and login belong in one plan")
        return self


def prepare_login_plan(
    scene: Observation, plan: LoginScreenPlan, policy: DecisionPolicy
) -> ActionPlan:
    if not isinstance(scene, SceneObservation):
        raise GuardStop("login_plan_requires_visual_scene")
    sources = {f.source for f in plan.fields}
    if sources != set(policy.facts.remaining_credentials):
        raise GuardStop("login_plan_missing_credentials")
    actions = [
        PlannedAction(
            Decision(
                screen_kind="login",
                operation="act",
                intent="credential",
                subgoal="Log in",
                reason="Execute observed credential plan",
                action=Choice(type="set_text", ref=f.ref, source=f.source),
            )
        )
        for f in plan.fields
    ]
    # Preflight the final command against the facts successful input delivery
    # will establish. Runtime still stops immediately if any input is rejected.
    after_delivery = DecisionPolicy(
        replace(
            policy.facts,
            submitted_credentials=tuple(sorted(set(policy.facts.submitted_credentials) | sources)),
        )
    )
    actions.append(
        PlannedAction(
            Decision(
                screen_kind="login",
                operation="act",
                intent="login",
                subgoal="Log in",
                reason="Submit the completed credential plan",
                action=Choice(type="click", ref=plan.login_ref),
            ),
            after_delivery,
        )
    )
    return ActionPlan(
        tuple(actions),
        "login",
        {
            "check": "login_screen_plan",
            "assignments": [f.model_dump() for f in plan.fields],
            "login_ref": plan.login_ref,
        },
    )


def prepare_screen_fill(scene: Observation, plan: ScreenFillPlan) -> ActionPlan:
    if not isinstance(scene, SceneObservation):
        raise GuardStop("screen_fill_requires_visual_scene")
    actions = [
        PlannedAction(
            Decision(
                screen_kind=scene.screen_kind,
                operation="act",
                intent="fill",
                subgoal=plan.purpose or "Fill visible screen",
                reason="Execute the current screen field plan",
                action=Choice(type="set_text", ref=f.ref, source=f.source),
            )
        )
        for f in plan.fields
    ]
    return ActionPlan(
        tuple(actions),
        "fields",
        {
            "check": "screen_fill_plan",
            "assignments": [f.model_dump() for f in plan.fields],
        },
    )


@dataclass(frozen=True)
class ScreenFieldVerificationRequest:
    execution: PlanExecution
    observation: Observation
    model_only_review: bool
    verified_fields: frozenset[str]
    visual_proposal: Any | None


@dataclass(frozen=True)
class ScreenFieldVerificationServices:
    input_surfaces: Callable[[str | None], set[str]]
    record_verified_field: Callable[[str, Any], bool]


@dataclass(frozen=True)
class ScreenFieldVerificationResult:
    payload: dict
    matched_refs: tuple[str, ...]
    clear_verified: tuple[str, ...]
    failed_decision: Decision | None = None
    failure_reason: str | None = None
    clear_visual_proposal: bool = False


def verify_screen_fields(
    request: ScreenFieldVerificationRequest,
    services: ScreenFieldVerificationServices,
) -> ScreenFieldVerificationResult:
    """Evaluate one observed batch without reaching into mutable worker state."""

    execution, observation = request.execution, request.observation
    delivered_fields = [
        delivered for delivered in execution.delivered if delivered["choice"].source is not None
    ]
    if request.model_only_review:
        matched, mismatched = [], []
        for delivered in delivered_fields:
            source = delivered["choice"].source
            if source in request.verified_fields:
                matched.append(source)
            else:
                mismatched.append(source)
        payload = {
            "check": "screen_fill_values",
            "ok": not mismatched,
            "matched": matched,
            "mismatched": mismatched,
            "delegated": [delivered["choice"].source for delivered in delivered_fields],
            "reviewer": "screen_model",
        }
        return ScreenFieldVerificationResult(
            payload=payload,
            matched_refs=tuple(
                delivered["choice"].ref
                for delivered in delivered_fields
                if delivered["choice"].source in request.verified_fields
            ),
            clear_verified=(),
            failure_reason="screen_fill_evidence_missing" if mismatched else None,
            clear_visual_proposal=not bool(
                request.visual_proposal is not None
                and (
                    getattr(request.visual_proposal, "steps", None)
                    or getattr(request.visual_proposal, "operation", None) == "complete"
                )
            ),
        )

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
        clear_verified=tuple(mismatched),
        failed_decision=failed_decision,
        failure_reason="screen_fill_value_mismatch" if mismatched else None,
    )
