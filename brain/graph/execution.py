"""One execution lifecycle for single actions and screen-sized plans.

Builders choose actions and verification strategy. Only this executor owns batch
preflight, step accounting, dispatch order and partial-delivery audit records.
The graph's verify node owns the one subsequent semantic observation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict
from uuid import uuid4

from sawon_interfaces import ActionBatch, Observation

from brain.observe import describe
from brain.runtime.storage import RunLog
from brain.visual.scene import SceneObservation

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
    exploration_action_key: NotRequired[str]
    exploration_goal_key: NotRequired[str]
    exploration_action: NotRequired[dict]


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
    matched_refs: set[str] = field(default_factory=set)


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
    plan = brain._reconcile_action_plan(plan)
    if not plan.actions:
        brain.log.append(
            "select",
            payload={
                "check": "action_plan_already_satisfied",
                "obs_id": str(brain.observation.obs_id),
                **plan.audit,
            },
        )
        return {"next": "review"}
    # The selection step already counted the first action (also for a cache hit).
    if brain.steps + len(plan.actions) - 1 > brain.settings.max_steps:
        raise GuardStop("max_steps")
    for action in plan.actions:
        brain._guard_action(action.decision, action.policy)
    if plan.audit:
        brain.log.append("select", payload={"obs_id": str(brain.observation.obs_id), **plan.audit})
    if _direct_fill_batch_available(brain, plan):
        return _execute_direct_fill_batch(brain, plan)
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


def _direct_fill_batch_available(brain: Brain, plan: ActionPlan) -> bool:
    return (
        plan.verification == "fields"
        and len(plan.actions) > 1
        and isinstance(brain.observation, SceneObservation)
        and callable(getattr(brain.relay, "act_batch", None))
        and all(
            planned.decision.intent == "fill"
            and planned.decision.action is not None
            and planned.decision.action.type == "set_text"
            for planned in plan.actions
        )
    )


def _execute_direct_fill_batch(brain: Brain, plan: ActionPlan) -> dict:
    """Wire one unchanged capture, then let the relay perform guarded direct fills."""

    assert isinstance(brain.observation, SceneObservation)
    before = brain.observation
    execution = PlanExecution(plan, before)
    fresh = brain.relay.observe()
    prepared = []
    try:
        for index, planned in enumerate(plan.actions):
            if index:
                brain.steps += 1
            decision, choice = planned.decision, planned.decision.action
            assert choice is not None
            brain.decision = decision
            progress_scope = (
                f"{brain.phase}:{brain._decision_policy().current_step}:{brain.business_revision}"
            )
            point_refined = brain.click_refinement.applies(brain, decision)
            action = brain._wire_action(
                choice,
                decision,
                fresh_observation=fresh,
                log_renewal=index == 0,
            )
            brain._log_action_requested(action, choice)
            if brain.settings.screen_matching:
                brain.saved_evidence.after_action(decision, before)
                brain.review = None
            prepared.append((planned, action, choice, decision, progress_scope, point_refined))
    finally:
        brain.click_refinement.bound = None

    batch = ActionBatch(batch_id=uuid4(), actions=[item[1] for item in prepared])
    response = brain.relay.act_batch(batch)
    brain.log.append(
        "act",
        payload={
            "stage": "batch_returned",
            "batch_id": str(batch.batch_id),
            "ok": response.ok,
            "requested": len(batch.actions),
            "returned": len(response.results),
            "error": "relay_action_failed" if response.error else None,
            "error_code": brain._relay_error_code(response.error),
            "post_obs_id": str(response.post_obs_id) if response.post_obs_id else None,
        },
        latency_ms=response.duration_ms,
    )
    if response.batch_id != batch.batch_id:
        raise GuardStop("relay_action_identity_mismatch")
    expected_ids = [item[1].root.action_id for item in prepared]
    returned_ids = [result.action_id for result in response.results]
    if returned_ids != expected_ids[: len(returned_ids)]:
        raise GuardStop("relay_action_identity_mismatch")

    for prepared_item, result in zip(prepared, response.results, strict=False):
        planned, action, choice, decision, progress_scope, point_refined = prepared_item
        brain._log_action_returned(action, result)
        if not result.ok:
            break
        pending = brain._accept_dispatched_action(
            action,
            decision,
            choice,
            progress_scope=progress_scope,
            point_refined=point_refined,
        )
        if planned.effect_source is not None:
            pending["effect_source"] = planned.effect_source
            pending["transition"] = pending["transition"].model_copy(
                update={"source": planned.effect_source}
            )
        execution.delivered.append(pending)

    if not response.ok or len(execution.delivered) != len(prepared):
        for delivered in execution.delivered:
            log_transition(brain.log, delivered)
        brain.pending = None
        return brain._fail("relay_action_failed")
    brain.pending = {**execution.delivered[-1], "execution": execution}
    return {"next": "verify"}
