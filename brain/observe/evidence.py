"""Resolve semantic evidence in the current observation, never by an old ref."""

from sawon_interfaces import Observation, UIElement

from .summary import ancestors, describe


def matching_elements(observation: Observation, descriptor: dict) -> list[UIElement]:
    return [
        item for item in observation.elements if describe(item, observation.elements) == descriptor
    ]


def is_record_input(element: UIElement, observation: Observation) -> bool:
    """Business input records describe form fields, not repeated detail rows."""
    return element.role.casefold() in {"edit", "combobox"} and not any(
        parent.role.casefold() in {"table", "datagrid", "list", "row", "dataitem", "listitem"}
        for parent in ancestors(element, observation.elements)
    )


def matching_delivered_input(
    observation: Observation, before: Observation, ref: str, descriptor: dict
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
        if e.enabled and is_record_input(e, observation) and overlap(old.bbox, e.bbox) >= 0.7
    ]
