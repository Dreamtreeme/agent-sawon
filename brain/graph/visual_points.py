"""Bind model-selected image points to one visual observation and semantic action."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from brain.visual.scene import SceneObservation

from .schemas import Decision


class PointStep(Protocol):
    target: int
    action: str
    intent: str
    point: Sequence[float] | None
    source: str | None


@dataclass(frozen=True)
class BoundVisualPoint:
    observation_id: str
    ref: str
    action: str
    intent: str
    point: tuple[float, float]


class VisualPointBindings:
    """Keep explicit visual points out of the general model decision contract."""

    def __init__(self) -> None:
        self._points: dict[tuple[str, str, str, str, str | None], BoundVisualPoint] = {}

    def bind(self, observation: SceneObservation, steps: Iterable[PointStep]) -> None:
        prefix = "e" + str(observation.obs_id.int) + "0"
        elements = {element.ref: element for element in observation.elements}
        points = {}
        for step in steps:
            if step.point is None:
                if step.action in {"click", "double_click"}:
                    raise ValueError("visual_click_point_required")
                continue
            ref = prefix + str(step.target)
            target = elements.get(ref)
            if target is None:
                raise ValueError("visual_click_point_unknown_target")
            x, y = step.point
            left, top, width, height = target.bbox
            if not (left <= x < left + width and top <= y < top + height):
                raise ValueError("visual_click_point_outside_target")
            action = "set_text" if step.action == "fill" else step.action
            source = step.source if step.action == "fill" else None
            bound = BoundVisualPoint(str(observation.obs_id), ref, action, step.intent, (x, y))
            points[(bound.observation_id, ref, action, step.intent, source)] = bound
        # Each proposal owns its bindings. A later plan on the same frame must
        # not inherit coordinates from a discarded plan.
        self._points = points

    def get(self, observation: SceneObservation, decision: Decision) -> BoundVisualPoint | None:
        action = decision.action
        if action is None or action.ref is None:
            return None
        return self._points.get(
            (str(observation.obs_id), action.ref, action.type, decision.intent, action.source)
        )
