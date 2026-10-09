"""Model selection boundary for one observed worker state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
class SelectionServices:
    selection_call: Callable[[str, type[BaseModel]], BaseModel]
    choose_decision: Callable[[str, type[BaseModel]], Decision]


@dataclass(frozen=True)
class SelectionResult:
    decision: Decision | None = None
    action_plan: ActionPlan | None = None
    save_review: SaveReviewResponse | None = None


def selection_schema(request: SelectionRequest) -> type[BaseModel]:
    if request.phase == "save_result":
        return SaveReviewResponse

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
        return LoginScreenPlan

    screen_fill = bool(
        request.visual_screen_fill
        and isinstance(scene, SceneObservation)
        and scene.screen_kind != "login"
        and request.phase in {"task", "enter"}
        and (request.phase == "task" or scene.screen_kind == "editor")
        and (request.phase == "task" or request.duplicate_clear)
        and request.policy.facts.remaining_fields
    )
    return ScreenFillPlan if screen_fill else request.policy.response_schema()


def select_candidate(
    request: SelectionRequest,
    schema: type[BaseModel],
    services: SelectionServices,
) -> SelectionResult:
    if schema is SaveReviewResponse:
        review = services.selection_call(request.prompt, schema)
        return SelectionResult(save_review=review)
    if schema is LoginScreenPlan:
        plan = services.selection_call(request.prompt + LOGIN_GUIDANCE, schema)
        if plan.fields:
            return SelectionResult(
                action_plan=prepare_login_plan(request.observation, plan, request.policy)
            )
        return SelectionResult(decision=plan.next_decision.to_decision())
    if schema is ScreenFillPlan:
        plan = services.selection_call(request.prompt + SCREEN_FILL_GUIDANCE, schema)
        if plan.fields:
            return SelectionResult(action_plan=prepare_screen_fill(request.observation, plan))
        return SelectionResult(decision=plan.next_decision.to_decision())
    return SelectionResult(decision=services.choose_decision(request.prompt, schema))
