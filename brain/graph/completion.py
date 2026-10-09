"""Evidence validation for generic-task and legacy query completion."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sawon_interfaces import Observation, UIElement

from brain.observe.rows import row_refs
from brain.observe.summary import ancestors
from brain.observe.values import field_matches

from .execution import GuardStop
from .schemas import Decision


class CompletionRetry(RuntimeError):
    """Current evidence is incomplete and can be replanned within the run."""


def element_value(element: UIElement) -> str:
    return element.value if element.value is not None else element.name


def subtree(observation: Observation, ref: str) -> list[UIElement]:
    return [
        element
        for element in observation.elements
        if element.ref == ref
        or ref in {item.ref for item in ancestors(element, observation.elements)}
    ]


@dataclass(frozen=True)
class TaskCompletionResult:
    fields: tuple[tuple[str, UIElement], ...]
    payload: dict


def evaluate_task_completion(
    observation: Observation,
    decision: Decision,
    values: Mapping[str, str],
    conditions: Sequence[str],
    *,
    model_only_review: bool,
) -> TaskCompletionResult:
    by_ref = {item.ref: item for item in observation.elements}
    evidence = {item.field: item.ref for item in decision.evidence}
    if len(evidence) != len(decision.evidence):
        raise CompletionRetry("incomplete_task_field_evidence")
    if len(set(evidence.values())) != len(evidence):
        raise GuardStop("task_field_ref_reused")
    fields = []
    for field, ref in evidence.items():
        element = by_ref.get(ref)
        actual = element.value if element is not None else None
        if element is None or (not model_only_review and not field_matches(field, actual, values)):
            raise CompletionRetry("task_field_mismatch:" + field)
        fields.append((field, element))

    condition_evidence: dict[int, list[str]] = {}
    for item in decision.completion_evidence:
        if item.condition_index >= len(conditions):
            raise GuardStop("task_completion_condition_unknown")
        element = by_ref.get(item.ref)
        if element is None or not any(
            value and str(value).strip()
            for value in (element.name, element.value, getattr(element, "help_text", None))
        ):
            raise GuardStop("task_completion_evidence_missing")
        condition_evidence.setdefault(item.condition_index, []).append(item.ref)
    if not condition_evidence:
        raise CompletionRetry("task_completion_evidence_required")
    return TaskCompletionResult(
        fields=tuple(fields),
        payload={
            "ok": True,
            "check": "task_completion_fields",
            "reviewer": "screen_model" if model_only_review else "runtime_value_check",
            "fields": evidence,
            "conditions": {conditions[index]: refs for index, refs in condition_evidence.items()},
            "obs_id": str(observation.obs_id),
        },
    )


def evaluate_final_query_completion(
    observation: Observation,
    decision: Decision,
    values: Mapping[str, str],
    identity_field: str,
    grid: Sequence[UIElement],
) -> dict:
    by_ref = {item.ref: item for item in grid}
    row = by_ref.get(decision.row_ref)
    rows = row_refs(observation.elements, grid_ref=decision.grid_ref)
    if row is None or row.ref not in rows:
        raise GuardStop("missing_final_row_evidence")
    matching_rows: set[str] = set()
    for item in grid:
        if not field_matches(identity_field, element_value(item), values):
            continue
        below_grid = False
        for parent in [*ancestors(item, observation.elements), item]:
            if parent.ref == decision.grid_ref:
                below_grid = True
            elif below_grid and parent.ref in rows:
                matching_rows.add(parent.ref)
                break
    if not matching_rows:
        raise CompletionRetry("final_evidence_needs_more")
    if matching_rows != {row.ref}:
        raise GuardStop("final_query_not_one_matching_row")

    row_elements = {item.ref: item for item in subtree(observation, row.ref)}
    evidence = {item.field: item.ref for item in decision.evidence}
    if set(evidence) != set(values) or len(evidence) != len(decision.evidence):
        raise CompletionRetry("incomplete_final_field_evidence")
    fields_by_ref: dict[str, set[str]] = {}
    for field, ref in evidence.items():
        fields_by_ref.setdefault(ref, set()).add(field)
    if any(
        len(fields) > 1 and fields != {"packages.count", "packages.unit"}
        for fields in fields_by_ref.values()
    ):
        raise GuardStop("final_field_ref_reused")
    for field, ref in evidence.items():
        element = row_elements.get(ref)
        if element is None or not field_matches(field, element_value(element), values):
            raise CompletionRetry("final_field_mismatch:" + field)
    return {
        "ok": True,
        "check": "final_query_fields",
        "fields": evidence,
        "grid_ref": decision.grid_ref,
        "row_ref": decision.row_ref,
        "obs_id": str(observation.obs_id),
    }
