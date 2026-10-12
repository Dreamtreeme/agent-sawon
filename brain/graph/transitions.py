"""Run-local action effects and bounded navigation-cycle evidence.

Delivery is never proof of authentication, saving, or task completion. This
contract records observation identities and known fact names, never input values
or descriptions copied from controls. Navigation callers supply semantic hashes
and a checkpoint token that changes only with the phase or new business facts.
"""

import hashlib
import json
from collections import deque
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from brain.vocabulary import ExecutableIntent

from .schemas import Decision, FieldName, TextSource

TransitionFact = (
    FieldName
    | Literal[
        "query_input_verified",
        "query_submitted",
        "duplicate_clear",
        "save_issued",
        "final_query_verified",
        "credential.username_submitted",
        "credential.password_submitted",
    ]
)


def expected_effect(decision: Decision) -> "ExpectedEffect":
    """Return the observable effect required for a delivered decision."""

    assert decision.action is not None
    if decision.action.type in {"set_text", "type_text"}:
        if decision.action.source == "credential.password":
            return ExpectedEffect.PROTECTED_INPUT_REREAD
        return ExpectedEffect.INPUT_VALUE_MATCHES
    return {
        "navigate": ExpectedEffect.NAVIGATION_CHANGE,
        "select_tab": ExpectedEffect.NAVIGATION_CHANGE,
        "login": ExpectedEffect.LOGIN_RESULT_OBSERVATION,
        "search": ExpectedEffect.SEARCH_RESULT_OBSERVATION,
        "save": ExpectedEffect.SAVE_ACKNOWLEDGEMENT,
        "confirm_save": ExpectedEffect.SAVE_ACKNOWLEDGEMENT,
    }.get(decision.intent, ExpectedEffect.OBSERVATION_ONLY)


class EvidenceClaim(BaseModel):
    """One model-grounded fact with an explicit lifecycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(min_length=1)
    surface_key: str = Field(min_length=64, max_length=64)
    observation_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    status: Literal["active", "superseded"] = "active"
    reason: str | None = None


class EvidenceLedger:
    """Keep model evidence durable without treating it as immutable truth."""

    def __init__(self) -> None:
        self._claims: list[EvidenceClaim] = []
        self._active: dict[str, EvidenceClaim] = {}

    @staticmethod
    def surface_key(descriptor: dict) -> str:
        canonical = json.dumps(
            descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def record(self, field: str, descriptor: dict, observation_id: str) -> tuple[str, ...]:
        """Update one business fact; similar screen descriptors cannot erase other facts."""

        surface_key = self.surface_key(descriptor)
        self.invalidate(field, "newer_observation")
        claim = EvidenceClaim(
            field=field,
            surface_key=surface_key,
            observation_id=observation_id,
            revision=len(self._claims) + 1,
        )
        self._claims.append(claim)
        self._active[field] = claim
        return ()

    def invalidate(self, field: str, reason: str) -> bool:
        claim = self._active.pop(field, None)
        if claim is None:
            return False
        replacement = claim.model_copy(update={"status": "superseded", "reason": reason})
        self._claims[claim.revision - 1] = replacement
        return True

    def invalidate_surface(self, descriptor: dict, reason: str) -> tuple[str, ...]:
        surface_key = self.surface_key(descriptor)
        invalidated = []
        for field, claim in tuple(self._active.items()):
            if claim.surface_key == surface_key and self.invalidate(field, reason):
                invalidated.append(field)
        return tuple(sorted(invalidated))

    def owner(self, descriptor: dict) -> str | None:
        """Return the active field that already owns this observed surface."""

        surface_key = self.surface_key(descriptor)
        return next(
            (field for field, claim in self._active.items() if claim.surface_key == surface_key),
            None,
        )

    def prompt_context(self) -> list[dict]:
        return [
            {
                "field": claim.field,
                "observation_id": claim.observation_id,
                "revision": claim.revision,
            }
            for claim in sorted(self._active.values(), key=lambda item: item.field)
        ]

    @property
    def history(self) -> tuple[EvidenceClaim, ...]:
        return tuple(self._claims)


class ExplorationAttempt(BaseModel):
    """A semantic state transition that produced no useful route."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state_key: str = Field(min_length=1)
    goal_key: str = Field(min_length=1)
    action_key: str = Field(min_length=1)
    action: dict
    outcome: Literal["no_change", "cycle"]


def exploration_action_key(
    action_type: str,
    *,
    keys: str | None = None,
    source: str | None = None,
    target_key: str | None = None,
) -> str:
    """Identify the attempted transition, excluding incidental focus anchors."""

    if action_type == "key":
        return "key:" + (keys or "")
    if action_type in {"set_text", "type_text"}:
        return f"{action_type}:source:{source or ''}"
    if action_type == "wait":
        return "wait"
    return f"{action_type}:target:{target_key or ''}"


class ExplorationLedger:
    """Bounded run-local memory of failed goal-directed transitions."""

    def __init__(self, limit: int = 64) -> None:
        self._attempts: deque[ExplorationAttempt] = deque(maxlen=limit)

    def record(self, attempt: ExplorationAttempt) -> None:
        if attempt not in self._attempts:
            self._attempts.append(attempt)

    def failed(self, state_key: str, goal_key: str, action_key: str) -> bool:
        return any(
            item.state_key == state_key
            and item.goal_key == goal_key
            and item.action_key == action_key
            for item in self._attempts
        )

    def prompt_context(self, state_key: str, goal_key: str) -> list[dict]:
        return [
            {"action_key": item.action_key, "action": item.action, "outcome": item.outcome}
            for item in self._attempts
            if item.state_key == state_key and item.goal_key == goal_key
        ]


class ExpectedEffect(StrEnum):
    INPUT_VALUE_MATCHES = "input_value_matches"
    PROTECTED_INPUT_REREAD = "protected_input_reread"
    NAVIGATION_CHANGE = "navigation_change"
    SEARCH_RESULT_OBSERVATION = "search_result_observation"
    SAVE_ACKNOWLEDGEMENT = "save_acknowledgement"
    LOGIN_RESULT_OBSERVATION = "login_result_observation"
    OBSERVATION_ONLY = "observation_only"


class ObservedEffect(StrEnum):
    UNCHECKED = "unchecked"
    NO_CHANGE = "no_change"
    CHANGED = "changed"
    INPUT_VALUE_MATCHES = "input_value_matches"
    PROTECTED_INPUT_REREAD = "protected_input_reread"


class ActionTransition(BaseModel):
    """An acknowledged action, optionally joined to its subsequent observation.

    ``new_facts`` must be provided explicitly by the owning verifier. In
    particular, a protected-input reread says only that the control was reread;
    it does not say what its value is or that authentication succeeded.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(min_length=1)
    before_obs_id: str = Field(min_length=1)
    after_obs_id: str | None = Field(default=None, min_length=1)
    intent: ExecutableIntent
    source: TextSource | None = None
    expected_effect: ExpectedEffect
    delivery: Literal["delivered"] = "delivered"
    observed_effect: ObservedEffect = ObservedEffect.UNCHECKED
    new_facts: tuple[TransitionFact, ...] = ()

    @model_validator(mode="after")
    def require_observation_for_effect(self) -> "ActionTransition":
        if self.observed_effect == ObservedEffect.UNCHECKED:
            if self.after_obs_id is not None or self.new_facts:
                raise ValueError("An unchecked action cannot carry observed facts")
        elif self.after_obs_id is None:
            raise ValueError("An observed effect requires a subsequent observation")
        if (
            self.source == "credential.password"
            and self.observed_effect == ObservedEffect.INPUT_VALUE_MATCHES
        ):
            raise ValueError("A protected password cannot have a verified value")
        return self

    def complete(
        self,
        after_obs_id: str,
        observed_effect: ObservedEffect,
        new_facts: tuple[TransitionFact, ...] = (),
    ) -> "ActionTransition":
        """Return a validated completed record without changing its pending form."""
        return type(self).model_validate(
            {
                **self.model_dump(),
                "after_obs_id": after_obs_id,
                "observed_effect": observed_effect,
                "new_facts": new_facts,
            }
        )


class NavigationHistory:
    """Detect repeated changed-state paths within one business checkpoint.

    A single return A-B-A is legitimate. A repeated suffix such as A-B-A-B or
    A-B-C-A-B-C is a cycle. No-op actions do not erase history, and neither does
    an unrecorded intermediate semantic state. New business evidence resets the
    scope through ``checkpoint_token``; the caller must not use screenshot IDs
    or mere navigation changes as that token.

    Detection is not latched: an alternative state is permitted, while another
    repeated suffix is reported so the caller can apply its existing limit.
    """

    def __init__(self, max_states: int = 12) -> None:
        if not 4 <= max_states <= 64:
            raise ValueError("Navigation history must retain between 4 and 64 states")
        self._states: deque[str] = deque(maxlen=max_states)
        self._checkpoint_token: str | None = None

    @property
    def states(self) -> tuple[str, ...]:
        """Expose a bounded immutable snapshot for run-local diagnostics."""
        return tuple(self._states)

    def observe(self, before_state: str, after_state: str, checkpoint_token: str) -> bool:
        if checkpoint_token != self._checkpoint_token:
            self._states.clear()
            self._checkpoint_token = checkpoint_token
        if before_state == after_state:
            return False
        if not self._states or self._states[-1] != before_state:
            self._states.append(before_state)
        self._states.append(after_state)
        states = tuple(self._states)
        return any(
            states[-2 * width : -width] == states[-width:]
            for width in range(2, len(states) // 2 + 1)
        )
