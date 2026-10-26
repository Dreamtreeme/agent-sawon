"""Compare observed navigation effects without volatile refs or focus metadata."""

import hashlib
import json
from pathlib import Path

from sawon_interfaces import Observation, UIElement

from brain.visual.frame_change import compare_frames

from .summary import ancestors
from .values import compact_normalize


def navigation_target(element: UIElement, observation: Observation) -> dict:
    """Identify a control semantically, keeping window chrome ancestry distinct."""
    return {
        "role": element.role,
        "name": element.name,
        "label": element.label_hint,
        "context": [
            {"role": parent.role, "name": parent.name, "label": parent.label_hint}
            for parent in ancestors(element, observation.elements)
        ],
    }


def semantic_target_key(target: dict) -> str:
    """Return a stable key when OCR spacing or duplicate label hints fluctuate."""
    return json.dumps(
        {
            "role": compact_normalize(target.get("role")),
            "name": compact_normalize(target.get("name")),
            "context": [
                {
                    "role": compact_normalize(parent.get("role")),
                    "name": compact_normalize(parent.get("name")),
                }
                for parent in target.get("context", [])
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def navigation_state(observation: Observation) -> str:
    """Identify a history bucket, not an action effect or screen-node identity."""
    capture_phash = getattr(observation, "capture_phash", None)
    if capture_phash is not None:
        # Model-selected fields can change between plans on identical pixels.
        # Those interpretation edits are not a navigation effect.
        return hashlib.sha256(
            json.dumps(
                {
                    "image": capture_phash,
                    "window": observation.active_window.handle
                    if observation.active_window
                    else None,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
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
