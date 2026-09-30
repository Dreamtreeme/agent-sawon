"""Resolve semantic evidence in the current observation, never by an old ref."""

from sawon_interfaces import Observation, UIElement

from .summary import ancestors, describe


def matching_elements(observation: Observation, descriptor: dict) -> list[UIElement]:
    return [
        item for item in observation.elements if describe(item, observation.elements) == descriptor
    ]


def is_record_input(element: UIElement, observation: Observation) -> bool:
    """The flat ShipmentRecord describes form fields, not repeated detail rows."""
    return element.role.casefold() in {"edit", "combobox"} and not any(
        parent.role.casefold() in {"table", "datagrid", "list", "row", "dataitem", "listitem"}
        for parent in ancestors(element, observation.elements)
    )
