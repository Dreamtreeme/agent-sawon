"""Run-local screen tree, indexed by semantic path and projected without stale coordinates."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .screen_mapping import ScreenMapping


def remember_screen(
    nodes: dict[str, dict], mapping: ScreenMapping, values: Mapping[str, str], observation_id: str
) -> dict | None:
    """Expand only visited nodes; absence in a new viewport never deletes discoveries."""
    node = None
    for depth in range(1, len(mapping.screen_path) + 1):
        path = mapping.screen_path[:depth]
        node = nodes.setdefault(
            json.dumps(path, ensure_ascii=False),
            {"path": path, "fields": {}, "controls": {}, "observed_inputs": {}},
        )
    if node is None:
        return None
    node["observation_id"] = observation_id
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
        "status": "awaiting_observation",
    }


def recall_screen_tree(nodes: Mapping[str, dict]) -> dict:
    """Derive a model view from the one state index; no refs/bboxes imply current visibility."""
    tree = {}
    for node in nodes.values():
        children = tree
        branch = None
        for name in node["path"]:
            branch = children.setdefault(name, {"fields": {}, "controls": {}, "children": {}})
            children = branch["children"]
        if branch is None:
            continue
        for source, field in node["fields"].items():
            entry = {
                "label": field["label"],
                "role": field["role"],
                "seen_in_last_view": field["observation_id"] == node.get("observation_id"),
            }
            observed = node["observed_inputs"].get(source)
            if observed is not None:
                entry["last_observation"] = {
                    "value": observed["value"],
                    "scope": observed["scope"],
                }
            if "delivery" in field:
                entry["delivery"] = {
                    key: field["delivery"][key] for key in ("value", "scope", "status")
                }
            branch["fields"][source] = entry
        branch["controls"] = {
            name: {
                "role": field["role"],
                "seen_in_last_view": field["observation_id"] == node.get("observation_id"),
            }
            for name, field in node.get("controls", {}).items()
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
        and delivery["status"] == "awaiting_observation"
    }
