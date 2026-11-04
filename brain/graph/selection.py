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
    SCREEN_FILL_GUIDANCE,
    ScreenFillPlan,
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
    selection_call: Callable[[str, type[BaseModel]], BaseModel | ActionPlan]
    choose_decision: Callable[[str, type[BaseModel]], Decision | ActionPlan]


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
    screen_fill = bool(
        request.visual_screen_fill
        and isinstance(scene, SceneObservation)
        and scene.screen_kind != "login"
        and request.phase in {"task", "enter"}
        and (request.phase == "task" or scene.screen_kind == "editor")
        and (request.phase == "task" or request.duplicate_clear)
        and request.policy.facts.remaining_fields
    )
    return ScreenFillPlan if login_batch or screen_fill else request.policy.response_schema()


def select_candidate(
    request: SelectionRequest,
    schema: type[BaseModel],
    services: SelectionServices,
) -> SelectionResult:
    if schema in {SaveReviewResponse, ScreenFillPlan}:
        guidance = SCREEN_FILL_GUIDANCE if schema is ScreenFillPlan else ""
        selected = services.selection_call(request.prompt + guidance, schema)
    else:
        selected = services.choose_decision(request.prompt, schema)
    if isinstance(selected, ActionPlan):
        return SelectionResult(action_plan=selected)
    if schema is SaveReviewResponse:
        return SelectionResult(save_review=selected)
    if schema is ScreenFillPlan:
        if selected.fields:
            return SelectionResult(action_plan=prepare_screen_fill(request.observation, selected))
        return SelectionResult(decision=selected.next_decision.to_decision())
    return SelectionResult(decision=selected)
