"""Identify observed grid rows without application names or stable selectors."""

from collections import defaultdict

from sawon_interfaces import UIElement

GRID_ROLES = frozenset({"datagrid", "table", "list"})
ROW_ROLES = frozenset({"dataitem", "row", "listitem"})
STRUCTURAL_ROW_ROLES = frozenset({"custom", "group", "pane"})
CELL_ROLES = frozenset({"dataitem", "cell"})


def row_refs(elements: list[UIElement], *, grid_ref: str | None = None) -> set[str]:
    """Return outer row refs, scoped to their nearest observed grid.

    Some providers expose a row as a generic container and its cells as
    DataItems. Infer only the narrow direct-grid-child shape: nonempty direct
    cells, with no nested data containers. Headers, mixed containers, and empty
    generic containers do not establish a row. A cell inside an established
    row cannot itself become a second row.
    """
    by_ref = {item.ref: item for item in elements}
    children: dict[str, list[UIElement]] = defaultdict(list)
    for item in elements:
        if item.parent_ref is not None:
            children[item.parent_ref].append(item)

    candidates = {item.ref for item in elements if item.role.casefold() in ROW_ROLES}
    for item in elements:
        if item.role.casefold() not in STRUCTURAL_ROW_ROLES:
            continue
        parent = by_ref.get(item.parent_ref)
        cells = children[item.ref]
        if (
            parent is None
            or parent.role.casefold() not in GRID_ROLES
            or not cells
            or any(cell.role.casefold() not in CELL_ROLES for cell in cells)
        ):
            continue
        pending = [descendant for cell in cells for descendant in children[cell.ref]]
        ambiguous = False
        while pending:
            descendant = pending.pop()
            if descendant.role.casefold() in (
                GRID_ROLES | ROW_ROLES | STRUCTURAL_ROW_ROLES | CELL_ROLES
            ):
                ambiguous = True
                break
            pending.extend(children[descendant.ref])
        if not ambiguous:
            candidates.add(item.ref)

    rows = set()
    for ref in candidates:
        parent_ref = by_ref[ref].parent_ref
        while parent_ref is not None:
            parent = by_ref[parent_ref]
            if parent.role.casefold() in GRID_ROLES:
                if grid_ref is None or parent.ref == grid_ref:
                    rows.add(ref)
                break
            if parent_ref in candidates:
                break
            parent_ref = parent.parent_ref
    return rows
