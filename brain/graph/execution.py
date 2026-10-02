"""One execution lifecycle for single actions and screen-sized plans.

Builders choose actions and verification strategy. Only this executor owns batch
preflight, step accounting, dispatch order and partial-delivery audit records.
The graph's verify node owns the one subsequent semantic observation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict

from sawon_interfaces import Observation

from brain.observe import describe
from brain.runtime.storage import RunLog

from .policy import DecisionPolicy
from .schemas import Choice, Decision, FieldName
from .transitions import ActionTransition, ObservedEffect, TransitionFact

if TYPE_CHECKING:
    from .engine import Brain


class GuardStop(RuntimeError):
    """A precise, non-secret reason suitable for durable evidence."""


class PendingAction(TypedDict):
    before_observation: Observation
    choice: Choice
    decision: Decision
    source_window_handle: int
    source_navigation_state: str
    transition: ActionTransition
    descriptor: NotRequired[dict]
    navigation_target: NotRequired[dict]
    click_target: NotRequired[dict]
    progress_scope: NotRequired[str]
    blur_recheck: NotRequired[bool]
    execution: NotRequired[PlanExecution]
    login_plan_descriptors: NotRequired[list[dict]]
    point_refined: NotRequired[bool]
    effect_source: NotRequired[FieldName]
    visual_match_candidate: NotRequired[tuple[str, dict[str, Any]]]


@dataclass(frozen=True)
class PlannedAction:
    decision: Decision
    policy: DecisionPolicy | None = None
    effect_source: FieldName | None = None


@dataclass(frozen=True)
class ActionPlan:
    actions: tuple[PlannedAction, ...]
    verification: Literal["single", "login", "fields"] = "single"
    audit: dict = field(default_factory=dict)

    @classmethod
    def single(cls, decision: Decision) -> ActionPlan:
        return cls((PlannedAction(decision),))


@dataclass
class PlanExecution:
    plan: ActionPlan
    before: Observation
    delivered: list[PendingAction] = field(default_factory=list)
    matched_fields: set[str] = field(default_factory=set)


def log_transition(
    log: RunLog,
    pending: PendingAction,
    observation: Observation | None = None,
    effect: ObservedEffect = ObservedEffect.UNCHECKED,
    facts: tuple[TransitionFact, ...] = (),
) -> None:
    transition = pending.get("transition")
    if transition is None:
        return
    if observation is not None and effect != ObservedEffect.UNCHECKED:
        transition = transition.complete(str(observation.obs_id), effect, facts)
    log.append(
        "verify",
        payload={"check": "action_transition", "transition": transition.model_dump(mode="json")},
    )


def execute_plan(brain: Brain, plan: ActionPlan) -> dict:
    assert brain.observation is not None
    if not plan.actions:
        raise GuardStop("empty_action_plan")
    # The selection step already counted the first action (also for a cache hit).
    if brain.steps + len(plan.actions) - 1 > brain.settings.max_steps:
        raise GuardStop("max_steps")
    for action in plan.actions:
        brain._guard_action(action.decision, action.policy)
    if plan.audit:
        brain.log.append("select", payload={"obs_id": str(brain.observation.obs_id), **plan.audit})
    execution = PlanExecution(plan, brain.observation)
    for index, action in enumerate(plan.actions):
        if index:
            brain.steps += 1
        brain.decision = action.decision
        outcome = brain._dispatch_action(screen_batch=index > 0)
        if outcome["next"] != "verify":
            for delivered in execution.delivered:
                log_transition(brain.log, delivered)
            brain.pending = None
            return outcome
        assert brain.pending is not None
        if action.effect_source is not None:
            brain.pending["effect_source"] = action.effect_source
            brain.pending["transition"] = brain.pending["transition"].model_copy(
                update={"source": action.effect_source}
            )
        execution.delivered.append(brain.pending)
    # Preserve each action's pending evidence; the last action owns group verification.
    brain.pending = {**execution.delivered[-1], "execution": execution}
    if plan.verification == "login":
        scene = execution.before
        by_ref = {e.ref: e for e in scene.elements}
        brain.pending["login_plan_descriptors"] = [
            describe(by_ref[a.decision.action.ref], scene.elements) for a in plan.actions
        ]
        for delivered in execution.delivered[:-1]:
            source = delivered["choice"].source
            brain.credential_submissions[source] = {
                "window_handle": scene.active_window.handle,
                "descriptor": delivered["descriptor"],
            }
    return {"next": "verify"}
