"""One execution lifecycle for single actions and screen-sized plans.

The worker prepares actions and owns policy/progress. This executor only sends
prepared requests and returns acknowledged deliveries, including partial failures.
The graph's verify node owns the one subsequent semantic observation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, TypedDict
from uuid import uuid4

from sawon_interfaces import Action, ActionBatch, ActionResult, Observation

from brain.relay import Relay
from sawon_runtime.storage import RunLog

from .policy import DecisionPolicy
from .schemas import Choice, Decision, FieldName
from .transitions import ActionTransition, ObservedEffect, TransitionFact


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
    delivered_point: NotRequired[tuple[float, float]]
    effect_source: NotRequired[FieldName]
    visual_match_candidate: NotRequired[tuple[str, dict[str, Any]]]
    exploration_action_key: NotRequired[str]
    exploration_goal_key: NotRequired[str]
    exploration_action: NotRequired[dict]
    post_save_identity_result_target: NotRequired[bool]
    taught_step_id: NotRequired[str]


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


@dataclass(frozen=True)
class PreparedAction:
    planned: PlannedAction
    wire: Action
    progress_scope: str
    point_refined: bool


@dataclass(frozen=True)
class ExecutionRequest:
    # Lazy preparation renews each sequential action's capture after the previous
    # action. Direct fill batches prepare against one shared capture instead.
    actions: Iterable[PreparedAction]
    batch: bool = False


@dataclass(frozen=True)
class ExecutionResult:
    delivered: tuple[PreparedAction, ...]
    error: Exception | None = None


def relay_error_code(error: str | None) -> str | None:
    known_errors = {
        "observation_expired",
        "capture_expired",
        "capture_unavailable",
        "capture_geometry_changed",
        "screen_changed",
        "window_changed",
        "foreground_not_allowed",
        "focus_not_confirmed",
        "point_outside_allowed_window",
        "point_outside_capture",
        "observation_stale",
        "observation_blocked",
        "action_failed",
        "unsupported_key",
    }
    return error if error in known_errors else None


def log_requested(log: RunLog, item: PreparedAction) -> None:
    logged = item.wire.model_dump(mode="json")
    source = item.planned.decision.action.source
    if source and source.startswith("credential."):
        logged["text"] = "[REDACTED]"
    log.append("act", payload={"stage": "requested", "action": logged})


def log_returned(log: RunLog, action: Action, response: ActionResult) -> None:
    log.append(
        "act",
        payload={
            "stage": "returned",
            "action_id": str(action.root.action_id),
            "ok": response.ok,
            "error": "relay_action_failed" if response.error else None,
            "error_code": relay_error_code(response.error),
            "post_obs_id": str(response.post_obs_id) if response.post_obs_id else None,
        },
        latency_ms=response.duration_ms,
    )


def execute_plan(request: ExecutionRequest, relay: Relay, log: RunLog) -> ExecutionResult:
    """No worker mutation or callbacks into policy; return only acknowledged prefix."""
    delivered = []
    try:
        if request.batch:
            prepared = list(request.actions)
            batch = ActionBatch(batch_id=uuid4(), actions=[item.wire for item in prepared])
            for item in prepared:
                log_requested(log, item)
            response = relay.act_batch(batch)
            log.append(
                "act",
                payload={
                    "stage": "batch_returned",
                    "batch_id": str(batch.batch_id),
                    "ok": response.ok,
                    "requested": len(prepared),
                    "returned": len(response.results),
                    "error": "relay_action_failed" if response.error else None,
                    "error_code": relay_error_code(response.error),
                    "post_obs_id": str(response.post_obs_id) if response.post_obs_id else None,
                },
                latency_ms=response.duration_ms,
            )
            expected = [item.wire.root.action_id for item in prepared]
            returned = [result.action_id for result in response.results]
            if response.batch_id != batch.batch_id or returned != expected[: len(returned)]:
                raise GuardStop("relay_action_identity_mismatch")
            for item, result in zip(prepared, response.results, strict=False):
                log_returned(log, item.wire, result)
                if not result.ok:
                    break
                delivered.append(item)
            if not response.ok or len(delivered) != len(prepared):
                raise GuardStop("relay_action_failed")
        else:
            for item in request.actions:
                log_requested(log, item)
                result = relay.act(item.wire)
                log_returned(log, item.wire, result)
                if result.action_id != item.wire.root.action_id:
                    raise GuardStop("relay_action_identity_mismatch")
                if not result.ok:
                    raise GuardStop("relay_action_failed")
                delivered.append(item)
    except Exception as error:
        return ExecutionResult(tuple(delivered), error)
    return ExecutionResult(tuple(delivered))
