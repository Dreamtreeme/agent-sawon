"""Canonical vocabulary and boundary mapping for user-supplied task inputs.

Inside the worker, an ``input key`` is always the exact key in ``inputs``.  A UI
action carries that key in ``source`` because credentials and business inputs
share the same execution channel.  Model-facing aliases are accepted only at
the screen-match boundary and are immediately converted to the canonical key.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING

from brain.observe.values import normalize

if TYPE_CHECKING:
    from brain.task_instruction import TaskInstruction

MODEL_INPUTS_NAME = "inputs"
LEGACY_INPUT_CONTAINERS = ("record.", "inputs.")
SOURCE_BOUND_INTENTS = frozenset({"credential", "query_input", "fill", "select_option"})


def allowed_input_surfaces(
    task: "TaskInstruction", source: str, inputs: Mapping[str, str]
) -> set[str]:
    """Resolve taught write permissions once for planning, execution and rereading."""
    surfaces = {
        action.surface or "direct_input"
        for step in task.procedure
        for action in step.actions
        if action.verb == "fill"
        and (resolve_input_key(action.input_key, inputs) or action.input_key) == source
    }
    if "any" in surfaces:
        return {"direct_input", "table_cell"}
    return surfaces or {"direct_input"}


def resolve_input_key(candidate: str | None, inputs: Mapping[str, str]) -> str | None:
    """Resolve one model-facing spelling to an exact canonical input key.

    Exact keys are authoritative. Container-qualified keys and unique literal
    values remain compatibility inputs for earlier prompts and providers. An
    ambiguous or unknown value is never guessed.
    """
    if candidate is None:
        return None
    if candidate in inputs:
        return candidate
    for prefix in LEGACY_INPUT_CONTAINERS:
        if candidate.startswith(prefix):
            key = candidate.removeprefix(prefix)
            if key in inputs:
                return key
    matching = [key for key, value in inputs.items() if normalize(value) == normalize(candidate)]
    return matching[0] if len(matching) == 1 else None


def normalize_action_source(
    *, intent: str, candidate: str | None, inputs: Mapping[str, str]
) -> str | None:
    """Return a canonical source key only for actions that bind supplied data."""
    if intent not in SOURCE_BOUND_INTENTS:
        return None
    if candidate is None or candidate.startswith("credential."):
        return candidate
    return resolve_input_key(candidate, inputs) or candidate
