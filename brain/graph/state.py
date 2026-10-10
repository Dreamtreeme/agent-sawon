"""작업자 그래프의 4대 단일 책임 노드 간 공식 인계 상태 계약."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypedDict
from uuid import UUID

from sawon_interfaces import Observation

from .click_refinement import ClickRefinement
from .execution import ActionPlan, PendingAction
from .method_runtime import MethodState
from .reconciliation import ReconciledObservation
from .review import EvidenceReview
from .saved_evidence import SavedEvidence
from .schemas import Decision
from .scratchpad import RollingContextManager
from .transitions import EvidenceLedger, ExplorationLedger, NavigationHistory
from .visual_points import VisualPointBindings

if TYPE_CHECKING:
    from .engine import Result
    from .matching import ScreenMatch

WorkerNode = Literal["observation", "decision", "execution", "review", "finish"]


class ObservationState(TypedDict):
    """관찰 책임 노드가 소유하는 현재 환경 관찰 데이터."""

    current: Observation | None
    sequence: int
    reconciled: ReconciledObservation | None


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


@dataclass
class ProgressState:
    """Run-local facts and histories; credentials, clients and files stay outside."""

    credential_submissions: dict[str, dict] = field(default_factory=dict)
    rolling_context: RollingContextManager = field(default_factory=RollingContextManager)
    verified: dict[str, dict] = field(default_factory=dict)
    evidence_ledger: EvidenceLedger = field(default_factory=EvidenceLedger)
    query_value_verified: bool = False
    query_submitted: bool = False
    query_descriptor: dict | None = None
    query_input_anchor: tuple[Observation, str] | None = None
    query_window_handle: int | None = None
    duplicate_clear: bool = False
    save_issued: bool = False
    post_save_query_identity_verified: bool = False
    model_saved_detail_review: bool = False
    post_save_verified: set[str] = field(default_factory=set)
    saved_evidence: SavedEvidence = field(default_factory=SavedEvidence)
    saved_bindings: tuple[str, dict[str, str]] | None = None
    saved_row_binding: tuple[str, str] | None = None
    click_refinement: ClickRefinement = field(default_factory=ClickRefinement)
    visual_points: VisualPointBindings = field(default_factory=VisualPointBindings)
    failures: dict[str, int] = field(default_factory=dict)
    phase_failures: dict[str, int] = field(default_factory=dict)
    progress_marks: set[str] = field(default_factory=set)
    business_revision: int = 0
    navigation_history: NavigationHistory = field(default_factory=NavigationHistory)
    unproductive_navigation: dict[str, list[dict]] = field(default_factory=dict)
    exploration: ExplorationLedger = field(default_factory=ExplorationLedger)
    _last_exploration_failure_key: str | None = None
    _last_surface_conflict: dict[str, str] | None = None
    feedback: list[dict] = field(default_factory=list)
    result: Result | None = None
    started: float = 0.0
    visual_match_candidate: tuple[str, dict] | None = None
    input_target_retries: int = 0
    deferred_field_checks: dict[str, dict] = field(default_factory=dict)
    visual_proposal: ScreenMatch | None = None
    visual_proposal_obs_id: UUID | None = None
    procedure_step: str | None = None
    active_taught_step: str | None = None
    completed_taught_steps: set[str] = field(default_factory=set)


def state_property(section: str, key: str) -> property:
    """Compatibility accessors address the state; no second copy is stored."""
    return property(
        lambda owner: owner._worker_state[section][key],
        lambda owner, value: owner._worker_state[section].__setitem__(key, value),
    )


def progress_property(key: str) -> property:
    return property(
        lambda owner: getattr(owner._worker_state["progress"], key),
        lambda owner, value: setattr(owner._worker_state["progress"], key, value),
    )


class WorkerState(TypedDict):
    """작업자 책임 노드 간 데이터 스냅샷 계약. 실행 중 서비스 객체는 포함하지 않는다."""

    observation: ObservationState
    decision: DecisionState
    execution: ExecutionState
    review: ReviewState
    lifecycle: LifecycleState
    progress: ProgressState
    methods: MethodState


def create_worker_state(
    *, rolling_buffer_size: int = 3, navigation_history_size: int = 8
) -> WorkerState:
    """초기 런타임 상태 생성."""
    return {
        "observation": {
            "current": None,
            "sequence": 0,
            "reconciled": None,
        },
        "methods": MethodState(),
        "progress": ProgressState(
            rolling_context=RollingContextManager(rolling_buffer_size),
            navigation_history=NavigationHistory(navigation_history_size),
        ),
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


__all__ = [
    "DecisionState",
    "ExecutionState",
    "LifecycleState",
    "ObservationState",
    "ReviewState",
    "WorkerNode",
    "WorkerState",
    "create_worker_state",
]
