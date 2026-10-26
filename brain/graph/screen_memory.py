"""Run-local screen tree, indexed by semantic path and projected without stale coordinates."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .screen_mapping import ScreenMapping


def remember_screen(
    nodes: dict[str, dict],
    mapping: ScreenMapping | None,
    values: Mapping[str, str],
    observation_id: str,
) -> dict:
    """Expand only visited nodes; absence in a new viewport never deletes discoveries."""
    screen_path = mapping.screen_path if mapping is not None else []
    # The root holds observations with no identified screen, never the previous screen's facts.
    for depth in range(len(screen_path) + 1):
        path = screen_path[:depth]
        node = nodes.setdefault(
            json.dumps(path, ensure_ascii=False),
            {"path": path, "fields": {}, "controls": {}},
        )
    if mapping is None:
        return node
    bindings = mapping.observed_bindings(values)
    for name, field in mapping.fields.items():
        if name in bindings:
            entry = node["fields"].setdefault(bindings[name], {})
            node["controls"].pop(name, None)
        else:
            # Buttons/tabs are remembered as discoveries, not invented child screens.
            entry = node["controls"].setdefault(name, {})
        entry.update(
            label=name, role=field.role, bbox=list(field.bbox), observation_id=observation_id
        )
    return node


def remember_input_delivery(
    nodes: dict[str, dict],
    path: list[str],
    source: str,
    value: str,
    *,
    observation_id: str,
    observation_number: int,
    scope: str,
) -> None:
    """Retain acknowledged input separately from observed values and business success."""
    node = nodes.get(json.dumps(path, ensure_ascii=False))
    if node is None or source not in node["fields"]:
        return
    node["fields"][source]["delivery"] = {
        "value": value,
        "scope": scope,
        "observation_id": observation_id,
        "observation_number": observation_number,
    }


def recall_screen_tree(nodes: Mapping[str, dict]) -> dict:
    """Derive a model view from the one state index; no refs/bboxes imply current visibility."""
    tree = {"fields": {}, "controls": {}, "children": {}}
    for node in nodes.values():
        branch = tree
        for name in node["path"]:
            branch = branch["children"].setdefault(
                name, {"fields": {}, "controls": {}, "children": {}}
            )
        for source, field in node["fields"].items():
            entry = {
                "label": field["label"],
                "role": field["role"],
            }
            observed = field.get("last_observation")
            if observed is not None:
                entry["last_observation"] = {
                    "value": observed["value"],
                    "scope": observed["scope"],
                }
            if "delivery" in field:
                entry["delivery"] = {key: field["delivery"][key] for key in ("value", "scope")}
            branch["fields"][source] = entry
        branch["controls"] = {
            name: {"role": field["role"]} for name, field in node.get("controls", {}).items()
        }
    return tree


def pending_screen_inputs(nodes: Mapping[str, dict], scope: str) -> set[str]:
    """Inputs delivered in this evidence scope, still awaiting an observation of their node."""
    return {
        source
        for node in nodes.values()
        for source, field in node["fields"].items()
        if (delivery := field.get("delivery"))
        and delivery["scope"] == scope
        and (
            (observed := field.get("last_observation")) is None
            or observed["scope"] != scope
            or observed["observation_number"] <= delivery["observation_number"]
        )
    }
