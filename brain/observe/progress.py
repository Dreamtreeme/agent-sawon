"""Compare observed navigation effects without volatile refs or focus metadata."""

import hashlib
import json

from sawon_interfaces import Observation, UIElement

from .summary import ancestors


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


def navigation_state(observation: Observation) -> str:
    """Hash the visible semantic tree, preserving duplicate nodes and ancestry.

    Coordinates, AutomationIds, screenshots, provider hashes, focus and ephemeral
    refs are not evidence that a navigation reached its intended destination.
    A different fingerprint is only an observed change, never task completion.
    """
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
