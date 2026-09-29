"""Run-local action effects and bounded navigation-cycle evidence.

Delivery is never proof of authentication, saving, or task completion. This
contract records observation identities and known fact names, never input values
or descriptions copied from controls. Navigation callers supply semantic hashes
and a checkpoint token that changes only with the phase or new business facts.
"""

from collections import deque
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import ExecutableIntent, FieldName, TextSource

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
