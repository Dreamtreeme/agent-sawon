"""Run-local delivered actions and observed effects, separate from business completion."""

import hashlib
import json
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

    def record(self, field: str, descriptor: dict, observation_id: str) -> None:
        """Update one business fact; similar screen descriptors cannot erase other facts."""

        surface_key = self.surface_key(descriptor)
        current = self._active.get(field)
        if (
            current is not None
            and current.observation_id == observation_id
            and current.surface_key == surface_key
        ):
            return
        self.invalidate(field, "newer_observation")
        claim = EvidenceClaim(
            field=field,
            surface_key=surface_key,
            observation_id=observation_id,
            revision=len(self._claims) + 1,
        )
        self._claims.append(claim)
        self._active[field] = claim
        return

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

    @property
    def history(self) -> tuple[EvidenceClaim, ...]:
        return tuple(self._claims)


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
    UNKNOWN = "unknown"
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
