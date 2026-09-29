"""Semantic summaries preserve relationships without stable IDs or coordinates."""

from __future__ import annotations

import json
from typing import Literal

from sawon_interfaces import Observation, UIElement

from .rows import row_refs

INTERACTIVE = {
    "edit",
    "button",
    "combobox",
    "checkbox",
    "radiobutton",
    "tab",
    "tabitem",
    "datagrid",
    "table",
    "dataitem",
    "list",
    "listitem",
    "menuitem",
    "treeitem",
    "header",
    "headeritem",
    "row",
    "cell",
    "hyperlink",
}
CONTAINERS = {"window", "pane", "group", "tab", "datagrid", "table", "list", "dataitem", "row"}


def redact(observation: Observation, secrets: tuple[str, ...]) -> Observation:
    def clean(value: str | None) -> str | None:
        if value is None:
            return None
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value

    # Only UI-provided strings can contain entered credentials. Never rewrite
    # refs, UUIDs, schema versions, timestamps or hashes when a short credential
    # happens to match protocol metadata: that would corrupt identity validation.
    data = observation.model_dump(mode="json")
    for item in data["elements"]:
        for field in ("name", "label_hint", "value", "automation_id"):
            if field in item:
                item[field] = clean(item[field])
    for window in [*data["windows"], data["active_window"]]:
        if window is not None:
            window["title"] = clean(window["title"])
            window["process"] = clean(window["process"])
    if data.get("blocked_reason") is not None:
        data["blocked_reason"] = clean(data["blocked_reason"])
    return Observation.model_validate(data)


def ancestors(element: UIElement, elements: list[UIElement]) -> list[UIElement]:
    by_ref = {item.ref: item for item in elements}
    result: list[UIElement] = []
    parent = element.parent_ref
    while parent is not None:
        ancestor = by_ref[parent]
        result.append(ancestor)
        parent = ancestor.parent_ref
    return list(reversed(result))


def describe(element: UIElement, elements: list[UIElement]) -> dict:
    return {
        "role": element.role,
        "name": element.name,
        "label": element.label_hint,
        "context": [
            {"role": parent.role, "name": parent.name}
            for parent in ancestors(element, elements)
            if parent.role.casefold() in CONTAINERS and parent.name
        ],
    }


def _nearest_label(element: UIElement, elements: list[UIElement]) -> str | None:
    if element.label_hint:
        return element.label_hint
    if element.name:
        return element.name
    # Geometry is used only to derive a semantic association in this observation.
    # Coordinates never become actions or durable target descriptors.
    x, y, width, height = element.bbox
    candidates = [
        other
        for other in elements
        if other.role.casefold() in {"text", "label"}
        and other.name
        and other.parent_ref == element.parent_ref
        and (other.bbox[0] <= x or other.bbox[1] <= y)
    ]
    if not candidates:
        return None
    nearest = min(
        candidates,
        key=lambda other: abs(other.bbox[1] - y) * 3 + abs(other.bbox[0] + other.bbox[2] - x),
    )
    if abs(nearest.bbox[1] - y) > max(80, height * 3):
        return None
    return nearest.name


def summarize(
    observation: Observation,
    strategy: Literal["flat_fields", "label_grouped"],
    *,
    max_elements: int = 500,
) -> str:
    rows = row_refs(observation.elements)
    selected = [
        item
        for item in observation.elements
        if item.role.casefold() in INTERACTIVE
        or item.ref in rows
        or (item.role.casefold() in {"text", "label"} and item.name)
    ]
    entries = []
    for element in selected[:max_elements]:
        entry = {
            "ref": element.ref,
            "role": element.role,
            "name": element.name,
            "value": element.value,
            "enabled": element.enabled,
            "focused": element.focused,
            "parent_ref": element.parent_ref,
        }
        chain = ancestors(element, observation.elements)
        row_ref = next((item.ref for item in reversed([*chain, element]) if item.ref in rows), None)
        if row_ref is not None:
            entry["row_ref"] = row_ref
        if element.ref in rows:
            entry["is_row"] = True
        # Stable IDs, process paths and coordinates deliberately never enter prompts.
        if strategy == "label_grouped":
            entry["label"] = _nearest_label(element, observation.elements)
            entry["region"] = [
                {"ref": parent.ref, "role": parent.role, "name": parent.name}
                for parent in chain
                if parent.role.casefold() in CONTAINERS or parent.ref in rows
            ]
        else:
            entry["label_hint"] = element.label_hint
        entries.append(entry)
    result = {
        "window": observation.active_window.title if observation.active_window else None,
        "elements": entries,
        "truncated": len(selected) > max_elements,
    }
    if strategy not in {"flat_fields", "label_grouped"}:
        raise ValueError("Unknown observation summary strategy")
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
