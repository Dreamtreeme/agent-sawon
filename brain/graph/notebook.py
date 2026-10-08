"""Persist the central runtime facts for screen-matching workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from brain.runtime.storage import atomic_write_json

if TYPE_CHECKING:
    from .engine import Brain


def remember(brain: Brain) -> dict:
    """중앙 런타임 사실을 영구 수첩에 기록 (자격증명 및 오래된 바인딩 제외)."""
    scene = brain.observation
    rolling = getattr(brain, "rolling_context", None)
    max_history = getattr(brain.settings, "max_feedback_history", 3)

    memo = {
        "goal": brain.goal,
        "plan": brain.plan,
        "phase": brain.phase,
        "task_path": (
            "task-instruction.json" if getattr(brain, "task_instruction", None) else None
        ),
        # [수정] 12개 누적 대신 설정된 제한(기본 3개)만큼만 피드백 유지
        "recent_feedback": brain.feedback[-max_history:],
        # [추가] 롤링 컨텍스트 매니저로부터 최근 턴 및 완료된 마일스톤 주입
        "recent_turns": (
            [t.model_dump(mode="json") for t in rolling.recent_turns] if rolling is not None else []
        ),
        "completed_milestones": (
            list(rolling._completed_milestones) if rolling is not None else []
        ),
        "observation_id": str(scene.obs_id) if scene else None,
        "observation_path": (
            f"observations/{brain.observation_number:04d}.json" if scene else None
        ),
        "result": brain.result.model_dump(mode="json") if brain.result else None,
    }

    if getattr(brain, "task_instruction", None) is not None:
        memo.update(
            {
                "inputs_path": "record.json",
                "proposed_capability": getattr(brain, "procedure_step", None),
                "available_inputs": sorted(brain.values),
                "verified_inputs": sorted(brain.verified),
                "remaining_inputs": sorted(set(brain.values) - set(brain.verified)),
            }
        )
        methods = getattr(brain, "methods", None)
        if methods is not None and methods.enabled:
            memo["method_progress"] = methods.context()
    else:
        memo.update(
            {
                "record_path": "record.json",
                "proposed_procedure_step": getattr(brain, "procedure_step", None),
                "verified_fields": sorted(brain.verified),
                "remaining_fields": sorted(set(brain.values) - set(brain.verified)),
                "query_value_verified": brain.query_value_verified,
                "query_submitted": brain.query_submitted,
                "duplicate_clear": brain.duplicate_clear,
                "save_issued": brain.save_issued,
            }
        )
    atomic_write_json(brain.case_dir / "task-notebook.json", memo)
    return memo
