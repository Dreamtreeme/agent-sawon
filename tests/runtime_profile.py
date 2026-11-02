"""Current ERP settings with synthetic tasks and isolated, opt-in memory files."""

from pathlib import Path

from brain.settings import BrainSettings
from brain.task_instruction import TaskInstruction


def current_settings(tmp_path: Path, *, task_instruction=None, **overrides) -> BrainSettings:
    settings = BrainSettings.load(Path(__file__).resolve().parents[1] / "config/brain.visual.toml")
    task = task_instruction or TaskInstruction(
        name="Synthetic task",
        objective="Enter and save the supplied business values.",
        completion_conditions=["The saved business values are visible."],
    )
    task_path = tmp_path / "synthetic-task.json"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(task.model_dump_json(), encoding="utf-8")
    return settings.model_copy(
        update={
            "task_path": task_path,
            "visual_match_cache": settings.visual_match_cache.model_copy(
                update={"enabled": False, "path": tmp_path / "visual-match-cache.json"}
            ),
            "methods": settings.methods.model_copy(
                update={
                    "enabled": False,
                    "path": tmp_path / "methods.json",
                    "promotion_path": tmp_path / "promoted-methods.json",
                }
            ),
            **overrides,
        }
    )
