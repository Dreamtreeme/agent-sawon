"""작업자 그래프의 4대 단일 책임 노드 간 공식 인계 상태 계약."""

from __future__ import annotations

from typing import Literal, TypedDict

from sawon_interfaces import Observation

from .execution import ActionPlan, PendingAction
from .reconciliation import ReconciledObservation
from .review import EvidenceReview
from .schemas import Decision

WorkerNode = Literal["observation", "decision", "execution", "review", "finish"]


class ObservationState(TypedDict):
    """관찰 책임 노드가 소유하는 현재 환경 관찰 데이터."""

    current: Observation | None
    sequence: int
    reconciled: ReconciledObservation | None
    rolling_turns: list[dict]  # [추가] 직전 N턴의 실행 델타 요약 목록


class DecisionState(TypedDict):
    """현재 작업 선택 및 컴파일된 다중 액션 실행 계획."""

    current: Decision | None
    action_plan: ActionPlan | None
    task_plan: list[str]


class ExecutionState(TypedDict):
    """중계에 전달되어 효과 관찰 및 검증을 대기 중인 액션."""

    pending: PendingAction | None
    authorized: bool


class ReviewState(TypedDict):
    """결과 증거 평가 및 결정론적 검토 대기 상태."""

    current: EvidenceReview | None
    decision_pending: bool
    execution_pending: bool


class LifecycleState(TypedDict):
    """결정론적 그래프 라우팅 및 비즈니스 진행 상태."""

    route: WorkerNode
    phase: str
    steps: int


class WorkerState(TypedDict):
    """작업자 책임 노드 간 직렬화 가능한 공식 상태 인계 계약."""

    observation: ObservationState
    decision: DecisionState
    execution: ExecutionState
    review: ReviewState
    lifecycle: LifecycleState


def create_worker_state() -> WorkerState:
    """초기 런타임 상태 생성."""
    return {
        "observation": {
            "current": None,
            "sequence": 0,
            "reconciled": None,
            "rolling_turns": [],  # [추가] 빈 롤링 버퍼로 초기화
        },
        "decision": {"current": None, "action_plan": None, "task_plan": []},
        "execution": {"pending": None, "authorized": False},
        "review": {
            "current": None,
            "decision_pending": False,
            "execution_pending": False,
        },
        "lifecycle": {
            "route": "observation",
            "phase": "duplicate_query",
            "steps": 0,
        },
    }


def copy_worker_state(state: WorkerState) -> WorkerState:
    """그래프 노드가 입력 상태를 직접 변조하지 않도록 섹션별 복사."""

    return {
        "observation": {
            **state["observation"],
            "rolling_turns": list(state["observation"]["rolling_turns"]),
        },
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