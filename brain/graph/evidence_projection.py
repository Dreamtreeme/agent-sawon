"""Pure projection of one editor observation into saved-record evidence candidates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sawon_interfaces import UIElement

from brain.observe.evidence import is_record_input
from brain.visual.scene import SceneObservation

from .review import Matcher, assign_result_fields


@dataclass(frozen=True)
class EditorProjectionRequest:
    observation: SceneObservation
    values: Mapping[str, str]
    bindings: Mapping[str, str]
    identity_field: str
    detail_identity_present: bool
    detail_window: int | None
    tab_continuity: bool
    last_hash: str | None
    last_window: int | None
    last_screen: str | None


@dataclass(frozen=True)
class EditorProjection:
    linked: bool
    identity: UIElement | None
    identity_conflict: bool
    automatic: tuple[tuple[str, UIElement], ...]
    explicit: tuple[tuple[str, UIElement], ...]
    ambiguous_fields: tuple[str, ...]


def _editor_inputs(
    observation: SceneObservation,
    values: Mapping[str, str],
    matches: Matcher,
) -> dict[str, UIElement]:
    return {
        element.ref: element
        for element in observation.elements
        if is_record_input(element, observation)
        or (
            element.role.casefold() in {"text", "cell"}
            and bool(element.value or element.name)
            and any(matches(name, element.value or element.name, values) for name in values)
        )
    }


def _editor_identity(
    inputs: Mapping[str, UIElement],
    request: EditorProjectionRequest,
    matches: Matcher,
) -> UIElement | None:
    identity = inputs.get(request.bindings.get(request.identity_field, ""))
    if identity is not None and matches(request.identity_field, identity.value, request.values):
        return identity
    exact = [
        element
        for element in inputs.values()
        if matches(request.identity_field, element.value, request.values)
    ]
    return exact[0] if len(exact) == 1 else identity


def _linked_without_visible_identity(request: EditorProjectionRequest) -> bool:
    unchanged = (
        request.last_hash == request.observation.screen_hash
        and request.last_window == request.observation.active_window.handle
    )
    return bool(
        request.detail_identity_present
        and request.detail_window == request.observation.active_window.handle
        and request.last_screen == "editor"
        and (request.tab_continuity or unchanged)
    )


def project_editor_evidence(
    request: EditorProjectionRequest,
    matches: Matcher,
) -> EditorProjection:
    inputs = _editor_inputs(request.observation, request.values, matches)
    identity = _editor_identity(inputs, request, matches)
    identity_matches = bool(
        identity is not None and matches(request.identity_field, identity.value, request.values)
    )
    identity_conflict = identity is not None and not identity_matches
    linked = identity_matches or (identity is None and _linked_without_visible_identity(request))
    if not linked:
        return EditorProjection(False, identity, identity_conflict, (), (), ())

    candidates = {
        name: {
            ref for ref, element in inputs.items() if matches(name, element.value, request.values)
        }
        for name in request.values
    }
    automatic = tuple((name, inputs[ref]) for name, ref in assign_result_fields(candidates).items())
    owners: dict[str, list[str]] = {}
    for name, ref in request.bindings.items():
        if name in request.values and ref in inputs:
            owners.setdefault(ref, []).append(name)
    explicit = tuple((names[0], inputs[ref]) for ref, names in owners.items() if len(names) == 1)
    ambiguous = tuple(name for names in owners.values() if len(names) > 1 for name in names)
    return EditorProjection(True, identity, False, automatic, explicit, ambiguous)
