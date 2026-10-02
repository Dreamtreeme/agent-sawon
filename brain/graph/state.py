"""State contract shared by the four worker responsibilities."""

from __future__ import annotations

from typing import Literal, TypedDict

from sawon_interfaces import Observation

from .execution import ActionPlan, PendingAction
from .review import EvidenceReview
from .schemas import Decision

WorkerNode = Literal["observation", "decision", "execution", "review", "finish"]


class ObservationState(TypedDict):
    """Current environment evidence owned by the observation responsibility."""

    current: Observation | None
    sequence: int


class DecisionState(TypedDict):
    """Current task choice and an optional compiled multi-action plan."""

    current: Decision | None
    action_plan: ActionPlan | None
    task_plan: list[str]


class ExecutionState(TypedDict):
    """Delivered action awaiting an observed, verified effect."""

    pending: PendingAction | None


class ReviewState(TypedDict):
    """Evidence assessment and a model decision awaiting deterministic review."""

    current: EvidenceReview | None
    decision_pending: bool


class LifecycleState(TypedDict):
    """Deterministic graph routing and business-workflow progress."""

    route: WorkerNode
    phase: str
    steps: int


class WorkerState(TypedDict):
    """Serializable handoff contract between worker responsibilities."""

    observation: ObservationState
    decision: DecisionState
    execution: ExecutionState
    review: ReviewState
    lifecycle: LifecycleState


def create_worker_state() -> WorkerState:
    return {
        "observation": {"current": None, "sequence": 0},
        "decision": {"current": None, "action_plan": None, "task_plan": []},
        "execution": {"pending": None},
        "review": {"current": None, "decision_pending": False},
        "lifecycle": {
            "route": "observation",
            "phase": "duplicate_query",
            "steps": 0,
        },
    }


def copy_worker_state(state: WorkerState) -> WorkerState:
    """Copy section dictionaries so a graph node never mutates its input state."""

    return {
        "observation": dict(state["observation"]),
        "decision": {
            **state["decision"],
            "task_plan": list(state["decision"]["task_plan"]),
        },
        "execution": dict(state["execution"]),
        "review": dict(state["review"]),
        "lifecycle": dict(state["lifecycle"]),
    }


__all__ = [
    "DecisionState",
    "ExecutionState",
    "LifecycleState",
    "ObservationState",
    "ReviewState",
    "WorkerNode",
    "WorkerState",
    "copy_worker_state",
    "create_worker_state",
]
