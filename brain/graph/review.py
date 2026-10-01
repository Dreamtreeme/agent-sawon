"""Classify current result evidence before offering a completion decision.

No refs or values from earlier observations are combined into current proof.
This preflight never replaces the final decision's per-field validation.
"""

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sawon_interfaces import Observation, UIElement

from brain.observe.rows import GRID_ROLES, row_refs
from brain.observe.summary import ancestors

Matcher = Callable[[str, str | None, dict[str, str]], bool]


class EvidenceReview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["0.1"] = "0.1"
    obs_id: str
    status: Literal["not_due", "ready", "needs_more", "blocked"]
    reason: str
    grid_ref: str | None = None
    row_ref: str | None = None
    missing_fields: tuple[str, ...] = ()
    matching_rows: int = 0


def _within(element: UIElement, parent: str, observation: Observation) -> bool:
    return element.ref == parent or any(
        item.ref == parent for item in ancestors(element, observation.elements)
    )


def _assign_fields(candidates: dict[str, set[str]]) -> dict[str, str]:
    """Maximum bipartite matching, so overlapping values cannot reuse a cell.

    An augmenting path can move an earlier field to another valid cell. Greedy
    first-fit matching would reject valid layouts when candidate sets overlap.
    """
    owner: dict[str, str] = {}

    def assign(field: str, visited: set[str]) -> bool:
        for ref in sorted(candidates[field]):
            if ref in visited:
                continue
            visited.add(ref)
            previous = owner.get(ref)
            if previous is None or assign(previous, visited):
                owner[ref] = field
                return True
        return False

    for field in sorted(candidates, key=lambda name: (len(candidates[name]), name)):
        assign(field, set())
    return {field: ref for ref, field in owner.items()}


def assign_result_fields(candidates: dict[str, set[str]]) -> dict[str, str]:
    """Assign the most fields, sharing a cell only for package count and unit."""
    best = _assign_fields(candidates)
    packages = {"packages.count", "packages.unit"}
    shared = candidates.get("packages.count", set()) & candidates.get("packages.unit", set())
    for ref in sorted(shared):
        other_candidates = {
            field: refs - {ref} for field, refs in candidates.items() if field not in packages
        }
        assignment = _assign_fields(other_candidates)
        assignment.update({field: ref for field in packages})
        if len(assignment) > len(best):
            best = assignment
    return best


def review_final(
    observation: Observation, values: dict[str, str], matches: Matcher
) -> EvidenceReview:
    """Require one current identified row; missing visibility is recoverable."""
    base = {"obs_id": str(observation.obs_id)}
    grids = [
        element
        for element in observation.elements
        if element.enabled and element.role.casefold() in GRID_ROLES
    ]
    identified: list[tuple[str, str]] = []
    rows_by_grid: dict[str, set[str]] = {}
    for grid in grids:
        rows = row_refs(observation.elements, grid_ref=grid.ref)
        rows_by_grid[grid.ref] = rows
        matched = [
            row
            for row in rows
            if any(
                _within(cell, row, observation)
                and matches("hbl_no", cell.value if cell.value is not None else cell.name, values)
                for cell in observation.elements
            )
        ]
        if len(matched) > 1:
            return EvidenceReview(
                **base,
                status="blocked",
                reason="final_query_multiple_matching_rows",
                grid_ref=grid.ref,
                matching_rows=len(matched),
            )
        identified.extend((grid.ref, row) for row in matched)
    if len(identified) > 1:
        return EvidenceReview(
            **base,
            status="needs_more",
            reason="ambiguous_result_grids",
            matching_rows=len(identified),
            missing_fields=tuple(sorted(values)),
        )
    grid_ref, row_ref = identified[0] if identified else (None, None)
    if row_ref is None:
        # A lone unidentified row can explain hidden fields, never prove its identity.
        diagnostic_rows = [(g, r) for g, rows in rows_by_grid.items() for r in rows]
        if len(diagnostic_rows) == 1:
            grid_ref, diagnostic_row = diagnostic_rows[0]
        else:
            diagnostic_row = None
        row_to_inspect = diagnostic_row
    else:
        row_to_inspect = row_ref
    cells = [
        cell
        for cell in observation.elements
        if row_to_inspect is not None and _within(cell, row_to_inspect, observation)
    ]
    candidates = {
        field: {
            cell.ref
            for cell in cells
            if matches(field, cell.value if cell.value is not None else cell.name, values)
        }
        for field in values
    }
    missing = tuple(sorted(set(values) - assign_result_fields(candidates).keys()))
    if row_ref is None:
        return EvidenceReview(
            **base,
            status="needs_more",
            reason="row_identifier_not_visible" if grids else "query_grid_not_observed",
            grid_ref=grid_ref,
            missing_fields=tuple(sorted(set(missing) | {"hbl_no"})),
        )
    return EvidenceReview(
        **base,
        status="needs_more" if missing else "ready",
        reason="current_row_fields_missing" if missing else "current_row_evidence_available",
        grid_ref=grid_ref,
        row_ref=row_ref,
        missing_fields=missing,
        matching_rows=1,
    )
