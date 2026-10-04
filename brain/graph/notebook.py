"""Persist the central runtime facts for screen-matching workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from brain.runtime.storage import atomic_write_json

if TYPE_CHECKING:
    from .engine import Brain


def remember(brain: Brain) -> dict:
    """Persist the central runtime facts without copying credentials or old bindings."""
    scene = brain.observation
    memo = {
        "goal": brain.goal,
        "plan": brain.plan,
        "phase": brain.phase,
        "task_path": (
            "task-instruction.json" if getattr(brain, "task_instruction", None) else None
        ),
        "procedure_path": "procedure.json" if getattr(brain, "procedure", None) else None,
        "recent_feedback": brain.feedback[-3:],
        "observation_id": str(scene.obs_id) if scene else None,
        "observation_path": f"observations/{brain.observation_number:04d}.json" if scene else None,
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
