"""Derive current workflow facts from an observation before choosing an action.

Action history is provenance.  It must not be the only way a visible state can
become true.  This module is deliberately pure so observation, decision and
execution keep separate responsibilities.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sawon_interfaces import Observation

from brain.observe.values import field_matches
from brain.visual.scene import SceneObservation

_TEXT_INPUT_ROLES = frozenset({"edit", "combobox"})


@dataclass(frozen=True)
class ObservedValueFact:
    """A value read from one current, executable input surface."""

    source: str
    ref: str
    obs_id: str
    screen_kind: str


@dataclass(frozen=True)
class ReconciledObservation:
    """Workflow-relevant facts derived only from the current observation."""

    obs_id: str
    query_input: ObservedValueFact | None = None


def reconcile_observation(
    observation: Observation,
    values: Mapping[str, object],
) -> ReconciledObservation:
    """Read conservative facts without using an intended or previous action.

    Query input promotion is intentionally strict: it requires a semantic query
    scene and exactly one enabled text input whose literal current value matches
    the requested identifier.  A matching table cell therefore cannot satisfy
    the input fact.
    """

    obs_id = str(observation.obs_id)
    if not isinstance(observation, SceneObservation) or observation.screen_kind != "query":
        return ReconciledObservation(obs_id=obs_id)
    if "hbl_no" not in values:
        return ReconciledObservation(obs_id=obs_id)

    matches = [
        element
        for element in observation.elements
        if element.enabled
        and element.role.casefold() in _TEXT_INPUT_ROLES
        and field_matches("hbl_no", element.value, values)
    ]
    if len(matches) != 1:
        return ReconciledObservation(obs_id=obs_id)
    return ReconciledObservation(
        obs_id=obs_id,
        query_input=ObservedValueFact(
            source="hbl_no",
            ref=matches[0].ref,
            obs_id=obs_id,
            screen_kind=observation.screen_kind,
        ),
    )


__all__ = ["ObservedValueFact", "ReconciledObservation", "reconcile_observation"]
