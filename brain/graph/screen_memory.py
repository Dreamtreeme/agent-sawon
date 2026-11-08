"""Run-local screen tree, indexed by semantic path and projected without stale coordinates."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from brain.observe.values import field_matches

if TYPE_CHECKING:
    from .screen_mapping import ScreenMapping


def remember_screen(
    nodes: dict[str, dict],
    mapping: ScreenMapping,
    values: Mapping[str, str],
    observation_id: str,
    *,
    observation_number: int,
    result_keys: Sequence[str] = (),
) -> dict:
    """Apply the model's current node, bindings and readings as a memory delta.

    Do not infer observations from the next action, matching input values or OCR
    handles. Omitted fields preserve discoveries from earlier viewports. Partial
    taught outcomes remain available even before the whole step is completed.
    """
    screen_path = mapping.screen_path
    # The root holds observations with no identified screen, never the previous screen's facts.
    for depth in range(len(screen_path) + 1):
        path = screen_path[:depth]
        node = nodes.setdefault(
            json.dumps(path, ensure_ascii=False),
            {"path": path, "fields": {}, "controls": {}},
        )
    sources = set(values) | set(result_keys)
    for name, field in mapping.fields.items():
        if field.source in sources:
            entry = node["fields"].setdefault(field.source, {})
            node["controls"].pop(name, None)
        else:
            # Buttons/tabs are remembered as discoveries, not invented child screens.
            entry = node["controls"].setdefault(name, {})
        entry.update(
            label=entry.get("label", name),
            role=field.role,
            bbox=list(field.bbox),
            observation_id=observation_id,
        )
        if field.source in sources and "value" in field.model_fields_set:
            entry["last_observation"] = {
                "value": field.value,
                "observation_id": observation_id,
                "observation_number": observation_number,
            }
    for result in mapping.observed_step_results:
        history = node.setdefault("observed_step_results", {}).setdefault(result.step_id, [])
        fact = {
            "observed_result": result.observed_result,
            "completed": result.completed,
            "observation_id": observation_id,
            "observation_number": observation_number,
        }
        if fact not in history:
            history.append(fact)
    return node


def collected_result(nodes: Mapping[str, dict], result_keys: Sequence[str]) -> dict:
    """Project requested facts from actual readings, preserving their screen provenance."""
    provenance = {key: [] for key in result_keys}
    for node in nodes.values():
        for source, field in node["fields"].items():
            observed = field.get("last_observation")
            if source in provenance and observed is not None:
                provenance[source].append(
                    {"screen_path": list(node["path"]), "label": field["label"], **observed}
                )
    values = {}
    for source, readings in provenance.items():
        readings.sort(key=lambda reading: reading["observation_number"])
        if readings and readings[-1]["value"].strip():
            values[source] = readings[-1]["value"]
    return {
        "values": values,
        "missing_keys": [source for source in result_keys if source not in values],
        "provenance": provenance,
    }


def remember_input_delivery(
    nodes: dict[str, dict],
    path: list[str],
    source: str,
    value: str,
    *,
    target: dict,
    observation_id: str,
    observation_number: int,
) -> None:
    """Retain acknowledged input separately from observed values and business success."""
    node = nodes.setdefault(
        json.dumps(path, ensure_ascii=False), {"path": list(path), "fields": {}, "controls": {}}
    )
    entry = node["fields"].setdefault(source, {})
    entry.update(target, label=entry.get("label", target["label"]))
    node["controls"].pop(target["label"], None)
    entry["delivery"] = {
        "value": value,
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
                    key: observed[key] for key in ("value", "observation_number")
                }
            if "delivery" in field:
                entry["delivery"] = {
                    key: field["delivery"][key] for key in ("value", "observation_number")
                }
            branch["fields"][source] = entry
        branch["controls"] = {
            name: {"role": field["role"]} for name, field in node.get("controls", {}).items()
        }
        if "observed_step_results" in node:
            branch["observed_step_results"] = {
                step_id: [
                    {
                        key: result[key]
                        for key in ("observed_result", "completed", "observation_number")
                    }
                    for result in history
                ]
                for step_id, history in node["observed_step_results"].items()
            }
    return tree


def _latest_inputs(nodes: Mapping[str, dict], fact: str) -> dict[str, dict]:
    latest = {}
    for node in nodes.values():
        for source, field in node["fields"].items():
            if fact in field and (
                source not in latest
                or field[fact]["observation_number"] >= latest[source][fact]["observation_number"]
            ):
                latest[source] = field
    return latest


def observed_inputs(nodes: Mapping[str, dict], values: Mapping[str, str]) -> dict[str, dict]:
    """Read matching observations; a newer delivery is not a new observation."""
    deliveries = _latest_inputs(nodes, "delivery")
    return {
        source: field
        for source, field in _latest_inputs(nodes, "last_observation").items()
        if source in values
        and field_matches(source, field["last_observation"]["value"], values)
        and (
            source not in deliveries
            or field["last_observation"]["observation_number"]
            > deliveries[source]["delivery"]["observation_number"]
        )
    }


def pending_screen_inputs(nodes: Mapping[str, dict]) -> set[str]:
    """Read delivered inputs that have no later observation of that source."""
    observations = _latest_inputs(nodes, "last_observation")
    return {
        source
        for source, field in _latest_inputs(nodes, "delivery").items()
        if source not in observations
        or observations[source]["last_observation"]["observation_number"]
        <= field["delivery"]["observation_number"]
    }
