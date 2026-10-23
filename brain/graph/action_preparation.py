"""Translate one grounded decision into a relay action."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sawon_interfaces import Action, Observation

from brain.relay import Relay
from brain.visual.scene import SceneObservation
from sawon_runtime.storage import RunLog

from .execution import GuardStop
from .policy import DecisionPolicy
from .schemas import Choice, Decision

TEXT_INPUT_ROLES = frozenset({"edit", "combobox", "dataitem", "cell"})


class ActionSurfaceConflict(GuardStop):
    def __init__(self, *, owner: str, source: str | None, ref: str | None) -> None:
        super().__init__("input_surface_claimed_by_other_field")
        self.owner, self.source, self.ref = owner, source, ref


@dataclass(frozen=True)
class ActionGuardRequest:
    observation: Observation
    decision: Decision
    policy: DecisionPolicy
    phase: str
    input_surface_allowed: bool = True
    surface_owner: str | None = None
    query_matches: bool = True
    exploration_blocked: bool = False


def validate_action_guard(request: ActionGuardRequest) -> None:
    decision, observation = request.decision, request.observation
    if not request.policy.allows(decision):
        raise GuardStop("action_not_allowed_by_state")
    choice = decision.action
    if choice is None:
        raise GuardStop("action_missing")
    by_ref = {item.ref: item for item in observation.elements}
    target = by_ref.get(choice.ref)
    point_only_scroll = bool(
        isinstance(observation, SceneObservation)
        and choice.type == "key"
        and choice.keys in {"pagedown", "pageup"}
    )
    if choice.type not in {"wait", "focus_window"} and (
        target is None or (not target.enabled and not point_only_scroll)
    ):
        raise GuardStop("target_not_current_or_enabled")
    if choice.type == "focus_window" and choice.handle not in {
        window.handle for window in observation.windows
    }:
        raise GuardStop("window_not_observed")
    if choice.type in {"set_text", "type_text"} and target.role.casefold() not in TEXT_INPUT_ROLES:
        raise GuardStop("target_not_text_input")
    if decision.intent == "fill" and (request.phase == "task" or decision.screen_kind == "editor"):
        if not request.input_surface_allowed:
            raise GuardStop("record_input_scope_mismatch")
        if request.surface_owner is not None and request.surface_owner != choice.source:
            raise ActionSurfaceConflict(
                owner=request.surface_owner,
                source=choice.source,
                ref=choice.ref,
            )
    if (
        request.phase != "task"
        and decision.intent == "search"
        and decision.screen_kind == "query"
        and not request.query_matches
    ):
        raise GuardStop("query_value_changed")
    if request.exploration_blocked:
        raise GuardStop("navigation_no_progress")


@dataclass(frozen=True)
class ActionWiringRequest:
    observation: Observation
    choice: Choice
    decision: Decision
    credentials: Mapping[str, str | None]
    values: Mapping[str, str]
    planned_point: tuple[float, float] | None = None
    refined_point: tuple[float, float] | None = None
    fresh_observation: Observation | None = None
    max_phash_distance: int = 16
    screen_batch: bool = False
    log_renewal: bool = True


@dataclass(frozen=True)
class ActionWiringServices:
    relay: Relay
    log: RunLog
    rebind_action: Callable[..., Any] | None = None


def _rebind_visual_point(
    request: ActionWiringRequest,
    services: ActionWiringServices,
    scene: SceneObservation,
    target,
    fresh: Observation,
    old_point: tuple[float, float],
    *,
    same_content: bool,
) -> tuple[tuple[float, float], str, int]:
    if same_content:
        return old_point, "exact_capture", 0
    if services.rebind_action is None:
        raise ValueError("visual_screen_changed_before_action")
    try:
        binding = services.rebind_action(
            scene,
            target,
            fresh,
            planned_point=old_point,
            max_phash_distance=request.max_phash_distance,
        )
    except ValueError as error:
        services.log.append(
            "verify",
            payload={
                "check": "visual_capture_renewal",
                "ok": False,
                "same_window": True,
                "same_content": False,
                "reason": str(error),
            },
        )
        raise ValueError("visual_screen_changed_before_action") from error
    return binding.point, binding.method, binding.phash_distance


def _log_visual_renewal(
    request: ActionWiringRequest,
    services: ActionWiringServices,
    previous: Observation,
    fresh: Observation,
    old_shot,
    new_shot,
    *,
    same_content: bool,
    binding_method: str,
    phash_distance: int,
) -> None:
    if not request.log_renewal:
        return
    services.log.append(
        "observe",
        payload={
            "check": (
                "visual_planned_capture_renewal"
                if request.screen_batch
                else "visual_identical_capture_renewal"
            ),
            "ok": True,
            "perceived_obs_id": str(previous.obs_id),
            "action_obs_id": str(fresh.obs_id),
            "screen_hash": fresh.screen_hash,
            "screen_batch": request.screen_batch,
            "origin_changed": new_shot.geometry.origin != old_shot.geometry.origin,
            "same_content": same_content,
            "binding_method": binding_method,
            "phash_distance": phash_distance,
        },
    )


def _visual_evidence_prefix(request: ActionWiringRequest, *, same_content: bool) -> str:
    if request.refined_point is not None:
        return "image_refined:"
    if request.planned_point is not None:
        return "image_planned:"
    return "ocr_visual:" if same_content else "ocr_rebound:"


def _fresh_visual_target(
    request: ActionWiringRequest,
    services: ActionWiringServices,
    scene: SceneObservation,
) -> tuple[dict, str]:
    choice = request.choice
    target = next(item for item in scene.elements if item.ref == choice.ref)
    x, y, width, height = target.bbox
    old_point = request.refined_point or request.planned_point or (x + width / 2, y + height / 2)
    fresh = request.fresh_observation or services.relay.observe()
    previous = scene.wire
    old_shot, new_shot = previous.screenshot, fresh.screenshot
    same_content = (
        old_shot.content_hash == new_shot.content_hash
        if old_shot is not None
        and new_shot is not None
        and old_shot.content_hash is not None
        and new_shot.content_hash is not None
        else fresh.screen_hash == previous.screen_hash
    )
    same_window = bool(
        fresh.active_window
        and previous.active_window
        and fresh.active_window.handle == previous.active_window.handle
        and fresh.active_window.process.casefold() == previous.active_window.process.casefold()
    )
    usable = bool(
        not fresh.blocked_reason
        and fresh.observation_mode == "visual_only"
        and same_window
        and new_shot is not None
        and new_shot.geometry is not None
        and old_shot is not None
        and old_shot.geometry is not None
    )
    if not usable:
        services.log.append(
            "verify",
            payload={
                "check": "visual_capture_renewal",
                "ok": False,
                "foreground_blocked": bool(fresh.blocked_reason),
                "same_window": same_window,
                "same_pixels": fresh.screen_hash == previous.screen_hash,
                "same_content": same_content,
                "reason": "capture_or_window_unusable",
            },
        )
        raise ValueError("visual_screen_changed_before_action")

    point, binding_method, phash_distance = _rebind_visual_point(
        request,
        services,
        scene,
        target,
        fresh,
        old_point,
        same_content=same_content,
    )
    _log_visual_renewal(
        request,
        services,
        previous,
        fresh,
        old_shot,
        new_shot,
        same_content=same_content,
        binding_method=binding_method,
        phash_distance=phash_distance,
    )
    return (
        {
            "image_point": list(point),
            "capture_id": str(new_shot.geometry.capture_id),
            "evidence": _visual_evidence_prefix(request, same_content=same_content)
            + target.ref
            + ":sha1:"
            + fresh.screen_hash,
            "source": "visual",
        },
        str(fresh.obs_id),
    )


def wire_action(request: ActionWiringRequest, services: ActionWiringServices) -> Action:
    choice, decision, observation = request.choice, request.decision, request.observation
    data = {
        "type": choice.type,
        "action_id": str(uuid4()),
        "obs_id": str(observation.obs_id),
        "reason": decision.intent,
    }
    if choice.type not in {"wait", "focus_window"}:
        data["target"], data["obs_id"] = _fresh_visual_target(request, services, observation)
        data["schema_version"] = "0.2"
    if choice.type in {"set_text", "type_text"}:
        data["text"] = request.credentials.get(choice.source) or request.values.get(
            choice.source, ""
        )
    elif choice.type == "key":
        if isinstance(observation, SceneObservation) and choice.keys in {"pagedown", "pageup"}:
            data["type"] = "scroll"
            data["wheel_delta"] = -600 if choice.keys == "pagedown" else 600
        else:
            data["keys"] = choice.keys
    elif choice.type == "focus_window":
        data["handle"] = choice.handle
    elif choice.type == "wait":
        data["duration_ms"] = choice.duration_ms
    return Action.model_validate(data)
