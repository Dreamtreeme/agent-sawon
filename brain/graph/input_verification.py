"""Pure post-action checks for one delivered text input."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sawon_interfaces import Observation, UIElement

from brain.observe.evidence import matching_delivered_input
from brain.observe.values import field_matches
from brain.visual.scene import SceneObservation

from .execution import PendingAction
from .schemas import Choice, Decision
from .transitions import ObservedEffect, TransitionFact


def delivered_input_element(
    pending: PendingAction,
    observation: Observation,
    choice: Choice,
    allowed_surfaces: set[str],
) -> UIElement | None:
    matches = matching_delivered_input(
        observation,
        pending["before_observation"],
        choice.ref,
        pending["descriptor"],
        allowed_surfaces=allowed_surfaces,
    )
    return matches[0] if len(matches) == 1 else None


def needs_blur_recheck(
    observation: Observation,
    element: UIElement,
    source: str | None,
    *,
    enabled: bool,
    credentials: Mapping[str, str | None],
    values: Mapping[str, str],
) -> bool:
    return bool(
        isinstance(observation, SceneObservation)
        and enabled
        and source not in credentials
        and element.focused
        and not field_matches(source, element.value, values)
    )


@dataclass(frozen=True)
class InputValueRequest:
    element: UIElement
    choice: Choice
    decision: Decision
    phase: str
    values: Mapping[str, str]
    credentials: Mapping[str, str | None]


@dataclass(frozen=True)
class InputValueResult:
    check: str | None = None
    observed_effect: ObservedEffect = ObservedEffect.NO_CHANGE
    failure: str | None = None
    progress: str | None = None
    verify_field: str | None = None
    query_verified: bool = False
    credential_submitted: bool = False
    new_facts: tuple[TransitionFact, ...] = ()


def evaluate_input_value(request: InputValueRequest) -> InputValueResult:
    source = request.choice.source
    if source == "credential.password":
        return InputValueResult(
            check="protected_input_reread",
            observed_effect=ObservedEffect.PROTECTED_INPUT_REREAD,
            credential_submitted=True,
            new_facts=("credential.password_submitted",),
        )
    if source == "credential.username":
        if request.element.value != request.credentials[source]:
            return InputValueResult(failure="credential_input_mismatch")
        return InputValueResult(
            check="input_value_matches",
            observed_effect=ObservedEffect.INPUT_VALUE_MATCHES,
            progress=source,
            credential_submitted=True,
            new_facts=("credential.username_submitted",),
        )
    if not field_matches(source, request.element.value, request.values):
        return InputValueResult(failure="input_value_mismatch")
    if request.decision.intent == "query_input":
        return InputValueResult(
            check="input_value_matches",
            observed_effect=ObservedEffect.INPUT_VALUE_MATCHES,
            progress="query_input",
            query_verified=True,
            new_facts=("query_input_verified",),
        )
    verify_field = (
        source
        if request.decision.intent == "fill"
        and (request.phase == "task" or request.decision.screen_kind == "editor")
        else None
    )
    return InputValueResult(
        check="input_value_matches",
        observed_effect=ObservedEffect.INPUT_VALUE_MATCHES,
        verify_field=verify_field,
        new_facts=(source,) if verify_field is not None else (),
    )
