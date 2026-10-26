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
    keys: str | None


def is_wheel_scroll(action: str, keys: str | None) -> bool:
    return action == "key" and keys in {"pagedown", "pageup"}


class VisualPointError(ValueError):
    """A rejected, entirely undispatched proposal with its offending coordinates."""

    def __init__(self, reason: str, detail: dict) -> None:
        super().__init__(reason)
        self.detail = {"type": reason, **detail}


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
        for index, step in enumerate(steps):
            ref = prefix + str(step.target)
            target = elements.get(ref)
            reason = None
            if step.point is None:
                if step.action in {"click", "double_click"}:
                    reason = "visual_click_point_required"
                else:
                    continue
            elif target is None:
                reason = "visual_click_point_unknown_target"
            else:
                x, y = step.point
                if is_wheel_scroll(step.action, step.keys):
                    # Wheel input acts at an image point, not inside the named field.
                    shot = observation.screenshot
                    if shot is None or not (0 <= x < shot.width and 0 <= y < shot.height):
                        reason = "visual_point_outside_capture"
                else:
                    left, top, width, height = target.bbox
                    if not (left <= x < left + width and top <= y < top + height):
                        reason = "visual_click_point_outside_target"
            if reason is not None:
                shot = observation.screenshot
                raise VisualPointError(
                    reason,
                    {
                        "loc": ["steps", index, "point"],
                        "action": step.action,
                        "keys": step.keys,
                        "target": step.target,
                        "target_name": target.name if target is not None else None,
                        "target_bbox": list(target.bbox) if target is not None else None,
                        "point": list(step.point) if step.point is not None else None,
                        "image_size": [shot.width, shot.height] if shot is not None else None,
                        "plan_delivery": "not_dispatched",
                        "delivered_actions": 0,
                    },
                )
            x, y = step.point
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
