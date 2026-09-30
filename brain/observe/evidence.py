"""Resolve semantic evidence in the current observation, never by an old ref."""

from sawon_interfaces import Observation, UIElement

from .summary import describe


def matching_elements(observation: Observation, descriptor: dict) -> list[UIElement]:
    return [
        item for item in observation.elements if describe(item, observation.elements) == descriptor
    ]
