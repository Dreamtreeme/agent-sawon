"""Model selection boundary for one observed worker state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel
from sawon_interfaces import Observation

from brain.visual.scene import SceneObservation

from .execution import ActionPlan
from .policy import DecisionPolicy
from .save_review import SaveReviewResponse
from .schemas import Decision
from .screen_batch import (
    LOGIN_GUIDANCE,
    SCREEN_FILL_GUIDANCE,
    LoginScreenPlan,
    ScreenFillPlan,
    prepare_login_plan,
    prepare_screen_fill,
)


@dataclass(frozen=True)
class SelectionRequest:
    observation: Observation
    phase: str
    duplicate_clear: bool
    query_submitted: bool
    visual_screen_fill: bool
    policy: DecisionPolicy
    prompt: str


@dataclass(frozen=True)
class SelectionMode:
    kind: Literal["save_review", "login_batch", "screen_fill", "decision"]
    schema: type[BaseModel]


@dataclass(frozen=True)
class SelectionServices:
    selection_call: Callable[[str, type[BaseModel]], BaseModel]
    choose_decision: Callable[[str, type[BaseModel]], Decision]


@dataclass(frozen=True)
class SelectionResult:
    decision: Decision | None = None
    action_plan: ActionPlan | None = None
    save_review: SaveReviewResponse | None = None


def selection_mode(request: SelectionRequest) -> SelectionMode:
    if request.phase == "save_result":
        return SelectionMode("save_review", SaveReviewResponse)
    scene = request.observation
    login_batch = bool(
        request.visual_screen_fill
        and isinstance(scene, SceneObservation)
        and scene.screen_kind == "login"
        and request.phase in {"task", "duplicate_query"}
        and not request.query_submitted
        and request.policy.facts.remaining_credentials
    )
    if login_batch:
        return SelectionMode("login_batch", LoginScreenPlan)
    screen_fill = bool(
        request.visual_screen_fill
        and isinstance(scene, SceneObservation)
        and scene.screen_kind != "login"
        and request.phase in {"task", "enter"}
        and (request.phase == "task" or scene.screen_kind == "editor")
        and (request.phase == "task" or request.duplicate_clear)
        and request.policy.facts.remaining_fields
    )
    if screen_fill:
        return SelectionMode("screen_fill", ScreenFillPlan)
    return SelectionMode("decision", request.policy.response_schema())


def select_candidate(
    request: SelectionRequest,
    mode: SelectionMode,
    services: SelectionServices,
) -> SelectionResult:
    if mode.kind == "save_review":
        review = services.selection_call(request.prompt, mode.schema)
        return SelectionResult(save_review=review)
    if mode.kind == "login_batch":
        plan = services.selection_call(request.prompt + LOGIN_GUIDANCE, mode.schema)
        if plan.fields:
            return SelectionResult(
                action_plan=prepare_login_plan(request.observation, plan, request.policy)
            )
        return SelectionResult(decision=plan.next_decision.to_decision())
    if mode.kind == "screen_fill":
        plan = services.selection_call(request.prompt + SCREEN_FILL_GUIDANCE, mode.schema)
        if plan.fields:
            return SelectionResult(action_plan=prepare_screen_fill(request.observation, plan))
        return SelectionResult(decision=plan.next_decision.to_decision())
    return SelectionResult(decision=services.choose_decision(request.prompt, mode.schema))
