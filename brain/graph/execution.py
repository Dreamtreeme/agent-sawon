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

from sawon_interfaces import Action, ActionBatch, ActionBatchResult, ActionResult, Observation

from brain.relay import Relay
from brain.vocabulary import SCROLL_DIRECTION_KEYS
from sawon_runtime.storage import RunLog

from .schemas import Choice, Decision, FieldName
from .transitions import ActionTransition, ObservedEffect, TransitionFact


class GuardStop(RuntimeError):
    """A precise, non-secret reason suitable for durable evidence."""


class PendingAction(TypedDict):
    before_observation: Observation
    choice: Choice
    decision: Decision
    structure_observation: NotRequired[dict]
    transition: ActionTransition
    descriptor: NotRequired[dict]
    blur_recheck: NotRequired[bool]
    execution: NotRequired[PlanExecution]
    login_plan_descriptors: NotRequired[list[dict]]
    effect_source: NotRequired[FieldName]
    visual_match_candidate: NotRequired[tuple[str, dict[str, Any]]]


@dataclass(frozen=True)
class PlannedAction:
    decision: Decision
    effect_source: FieldName | None = None


@dataclass(frozen=True)
class ActionPlan:
    actions: tuple[PlannedAction, ...]
    verification: Literal["single", "fields"] = "single"
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


@dataclass(frozen=True)
class ExecutionRequest:
    # Visual plans prepare all coordinates and values against one shared capture
    # before delivery. Nonvisual actions retain individual preparation.
    actions: Iterable[PreparedAction]
    batch: bool = False


@dataclass(frozen=True)
class ExecutionResult:
    delivered: tuple[PreparedAction, ...]
    error: Exception | None = None
    failed_action: PreparedAction | None = None
    response: ActionResult | ActionBatchResult | None = None


def describe_action(
    decision: Decision, before: Observation | None = None, *, wire: Action | None = None
) -> dict:
    """Describe the chosen target without copying input or credential text."""
    choice = decision.action
    target = (
        next((element for element in before.elements if choice and element.ref == choice.ref), None)
        if before is not None
        else None
    )
    result = {
        "intent": decision.intent,
        "source": choice.source if choice else None,
        "screen_kind": decision.screen_kind,
        "action": choice.type if choice else None,
        "target": (target.name or target.label_hint) if target else None,
    }
    if choice is not None:
        if choice.type == "focus_window":
            result["handle"] = choice.handle
            window = (
                next((window for window in before.windows if window.handle == choice.handle), None)
                if before is not None
                else None
            )
            result["target"] = window.title if window else f"window:{choice.handle}"
        elif choice.type == "open_app":
            result["app_id"] = choice.app_id
            app = (
                next((app for app in before.available_apps if app.id == choice.app_id), None)
                if before is not None
                else None
            )
            result["target"] = app.name if app else f"app:{choice.app_id}"
        elif choice.type == "key":
            directions = {key: value for value, key in SCROLL_DIRECTION_KEYS.items()}
            if choice.keys in directions:
                result.update(action="scroll", direction=directions[choice.keys])
            else:
                result["keys"] = choice.keys
    if wire is not None:
        payload = wire.model_dump(mode="json")
        # These are the actual request's coordinates/identity, never its text value.
        result.update(
            action_id=payload["action_id"], obs_id=payload["obs_id"], action=payload["type"]
        )
        for key in ("target", "wheel_delta", "duration_ms", "handle", "app_id", "keys"):
            if key in payload:
                result["wire_target" if key == "target" else key] = payload[key]
    return result


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
            "attempted": response.attempted,
            "details": response.details,
            "post_obs_id": str(response.post_obs_id) if response.post_obs_id else None,
        },
        latency_ms=response.duration_ms,
    )


def execute_plan(request: ExecutionRequest, relay: Relay, log: RunLog) -> ExecutionResult:
    """No worker mutation or callbacks into policy; return only acknowledged prefix."""
    delivered = []
    failed_action = None
    failure_response = None
    try:
        if request.batch:
            prepared = list(request.actions)
            batch = ActionBatch(batch_id=uuid4(), actions=[item.wire for item in prepared])
            for item in prepared:
                log_requested(log, item)
            response = relay.act_batch(batch)
            failure_response = response
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
                    failure_response = result
                    if result.details.get("stage") in {"batch_preflight", "batch_settle"}:
                        failed_action = next(
                            (
                                candidate
                                for candidate in prepared
                                if str(candidate.wire.root.action_id)
                                == result.details.get("failed_action_id")
                            ),
                            None,
                        )
                    elif result.details.get("stage") == "batch_delivery":
                        failed_action = item
                    break
                delivered.append(item)
            if not response.ok or len(delivered) != len(prepared):
                if failure_response is None:
                    failure_response = response
                raise GuardStop("relay_action_failed")
        else:
            for item in request.actions:
                failed_action = item
                log_requested(log, item)
                result = relay.act(item.wire)
                failure_response = result
                log_returned(log, item.wire, result)
                if result.action_id != item.wire.root.action_id:
                    raise GuardStop("relay_action_identity_mismatch")
                if not result.ok:
                    raise GuardStop("relay_action_failed")
                delivered.append(item)
                failed_action = failure_response = None
    except Exception as error:
        return ExecutionResult(tuple(delivered), error, failed_action, failure_response)
    return ExecutionResult(tuple(delivered))
