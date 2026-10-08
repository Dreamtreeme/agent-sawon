"""Resolve semantic evidence in the current observation, never by an old ref."""

from collections.abc import Iterable

from sawon_interfaces import Observation, UIElement

from .summary import ancestors, describe


def matching_elements(observation: Observation, descriptor: dict) -> list[UIElement]:
    return [
        item for item in observation.elements if describe(item, observation.elements) == descriptor
    ]


def input_surface(role: str, parent_roles: Iterable[str]) -> str | None:
    """Classify a grounded input by its semantics, independently of detector geometry."""
    role = role.casefold()
    if role in {"cell", "dataitem"}:
        return "table_cell"
    if role not in {"edit", "combobox"}:
        return None
    if any(
        parent.casefold() in {"table", "datagrid", "list", "row", "dataitem", "listitem"}
        for parent in parent_roles
    ):
        return "table_cell"
    return "direct_input"


def observed_input_surface(element: UIElement, observation: Observation) -> str | None:
    return input_surface(
        element.role, (parent.role for parent in ancestors(element, observation.elements))
    )


def is_record_input(element: UIElement, observation: Observation) -> bool:
    """Legacy record inputs are standalone form fields."""
    return observed_input_surface(element, observation) == "direct_input"


def matching_delivered_input(
    observation: Observation,
    before: Observation,
    ref: str,
    descriptor: dict,
    *,
    allowed_surfaces: Iterable[str] = ("direct_input",),
) -> list[UIElement]:
    """Link an immediately verified visual input by its observed extent, not changing labels."""
    from brain.visual.scene import SceneObservation, overlap

    if not isinstance(before, SceneObservation) or not isinstance(observation, SceneObservation):
        return matching_elements(observation, descriptor)
    if (
        before.active_window != observation.active_window
        or before.screen_kind != observation.screen_kind
        or before.screenshot.geometry.model_dump(exclude={"capture_id", "captured_at"})
        != observation.screenshot.geometry.model_dump(exclude={"capture_id", "captured_at"})
    ):
        return []
    old = next((e for e in before.elements if e.ref == ref), None)
    if old is None:
        return []
    return [
        e
        for e in observation.elements
        if e.enabled
        and observed_input_surface(e, observation) in allowed_surfaces
        and overlap(old.bbox, e.bbox) >= 0.7
    ]
