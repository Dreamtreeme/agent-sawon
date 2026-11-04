"""Compare observed navigation effects without volatile refs or focus metadata."""

import hashlib
import json
from pathlib import Path

from sawon_interfaces import Observation

from brain.visual.frame_change import compare_frames

from .summary import ancestors


def navigation_state(observation: Observation) -> str:
    """Fingerprint injected semantic observations that have no captured image."""
    nodes = []
    for element in observation.elements:
        nodes.append(
            json.dumps(
                {
                    "role": element.role,
                    "name": element.name,
                    "label": element.label_hint,
                    "value": element.value,
                    "enabled": element.enabled,
                    "context": [
                        [parent.role, parent.name, parent.label_hint]
                        for parent in ancestors(element, observation.elements)
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    data = {
        "window": (observation.active_window.model_dump() if observation.active_window else None),
        "nodes": sorted(nodes),
    }
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def compare_navigation(before: Observation, after: Observation, case_dir: Path) -> dict:
    """Compare actual captures independently of the model's changing interpretation."""
    old_window = before.active_window.handle if before.active_window else None
    new_window = after.active_window.handle if after.active_window else None
    if old_window != new_window:
        return {"method": "window", "changed": True, "reason": "window_changed"}
    if all(getattr(scene, "capture_phash", None) is None for scene in (before, after)):
        # Injected semantic observations without captured perception retain their contract.
        return {
            "method": "semantic",
            "changed": navigation_state(before) != navigation_state(after),
            "reason": "semantic_observations",
        }
    return compare_frames(
        case_dir / "visual" / str(before.obs_id) / "capture.png",
        case_dir / "visual" / str(after.obs_id) / "capture.png",
    )
