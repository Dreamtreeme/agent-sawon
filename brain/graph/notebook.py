"""Persist the central runtime facts for screen-matching workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sawon_runtime.storage import atomic_write_json

if TYPE_CHECKING:
    from .engine import Brain


def remember(brain: Brain) -> dict:
    """중앙 런타임 사실을 영구 수첩에 기록 (자격증명 및 오래된 바인딩 제외)."""
    scene = brain.observation
    rolling = brain.rolling_context
    max_history = brain.settings.max_feedback_history

    memo = {
        "goal": brain.goal,
        "plan": brain.plan,
        "phase": brain.phase,
        "task_path": "task-instruction.json",
        "recent_feedback": brain.feedback[-max_history:],
        "recent_turns": (
            [t.model_dump(mode="json") for t in rolling.recent_turns] if rolling is not None else []
        ),
        "completed_milestones": (rolling.completed_milestones if rolling is not None else []),
        "observation_id": str(scene.obs_id) if scene else None,
        "observation_path": (
            f"observations/{brain.observation_number:04d}.json" if scene else None
        ),
        "result": brain.result.model_dump(mode="json") if brain.result else None,
    }

    memo.update(
        {
            "inputs_path": "record.json",
            "proposed_capability": brain.procedure_step,
            "available_inputs": sorted(brain.values),
            "verified_inputs": sorted(brain.verified),
            "observed_inputs": brain.observed_inputs,
            "screen_nodes": brain.screen_nodes,
            "current_screen_path": brain.current_screen_path,
            "remaining_inputs": sorted(set(brain.values) - set(brain.verified)),
        }
    )
    if brain.memory_retrieval.enabled:
        memo["method_progress"] = brain.memory_retrieval.context()
    atomic_write_json(brain.case_dir / "task-notebook.json", memo)
    return memo
