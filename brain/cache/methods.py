"""Learned action methods, parameterized inputs, and current-screen target binding."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import product
from math import log
from pathlib import Path
from typing import Any, Literal

import portalocker
from pydantic import BaseModel, ConfigDict, Field, model_validator

from brain.observe.values import field_matches, normalize
from brain.visual.action_rebind import hash_distance
from brain.visual.markers import marker_role_allowed
from brain.visual.scene import ControlMeaning, contains
from sawon_runtime.storage import atomic_write_json

# Completion evidence still binds visible values through label-to-control geometry.
# A large score means the matching label belongs to a different part of the form.
MAX_RELATIVE_GEOMETRY_SCORE = 2.0


class MethodSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    description: str
    source_key: str


class MethodTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    role: str
    anchor_texts: list[str] = Field(default_factory=list)
    relation: Literal["inside", "right", "left", "below", "above"] | None = None
    marker_source: str | None = None
    anchor_geometry: tuple[float, float, float, float] | None = None
    point: tuple[float, float] | None = None
    bbox: tuple[int, int, int, int] | None = None

    @model_validator(mode="after")
    def has_rebind_strategy(self):
        if self.point is not None and self.bbox is not None:
            return self
        if not self.anchor_texts or self.relation is None:
            raise ValueError("method_target_requires_coordinate_or_anchor_relation")
        return self


class MethodAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: str
    action: Literal["fill", "click", "double_click", "key"]
    intent: str
    slot: str | None = None
    keys: str | None = None
    point: tuple[float, float] | None = None


class MethodActionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    purpose: str = ""
    taught_step_id: str | None = None
    trace_index: int | None = Field(default=None, ge=0)
    predecessor_group_ids: list[str] = Field(default_factory=list)
    context_anchors: list[str] = Field(default_factory=list)
    targets: list[MethodTarget] = Field(min_length=1)
    actions: list[MethodAction] = Field(min_length=1)
    expected_result: str = ""
    screen_kind: str = "unknown"
    screen_phash: int | None = None
    image_size: tuple[int, int] | None = None
    # Screen memory includes passive fields, not just the next action's targets.
    # Only meaning/geometry/source bindings persist; values come from the current OCR.
    observed_fields: list[MethodTarget] = Field(default_factory=list)
    field_sources: dict[str, str] = Field(default_factory=dict)
    save_result: (
        Literal["rejected", "confirmation_required", "ready_for_query", "unknown"] | None
    ) = None
    # Names in targets, never the detector IDs from the source run.
    message_targets: list[str] = Field(default_factory=list)
    repair_fields: list[str] = Field(default_factory=list)


class MethodCompletionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    slot: str
    target: MethodTarget
    condition_indexes: list[int] = Field(min_length=1)


class MethodCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    purpose: str = ""
    taught_step_id: str | None = None
    targets: list[MethodCompletionTarget] = Field(min_length=1)
    expected_result: str = ""
    screen_kind: str = "unknown"


class LearnedMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    purpose: str
    task_objective: str = ""
    task_contract_sha256: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    artifact_kind: Literal["runtime_trace", "warm_compiled"] = "runtime_trace"
    slots: list[MethodSlot] = Field(default_factory=list)
    action_groups: list[MethodActionGroup] = Field(min_length=1)
    completion: MethodCompletion | None = None
    source_transitions: list[str] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def valid_trace_graph(self):
        group_ids = [group.id for group in self.action_groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("method_action_group_ids_must_be_unique")
        indexes = [
            group.trace_index for group in self.action_groups if group.trace_index is not None
        ]
        if len(indexes) != len(set(indexes)):
            raise ValueError("method_trace_indexes_must_be_unique")
        known = set(group_ids)
        for group in self.action_groups:
            predecessors = group.predecessor_group_ids
            if len(predecessors) != len(set(predecessors)) or group.id in predecessors:
                raise ValueError("method_predecessors_must_be_unique_and_not_self")
            if not set(predecessors) <= known:
                raise ValueError("method_predecessor_unknown")
        return self


class MethodDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["0.1"] = "0.1"
    methods: list[LearnedMethod] = Field(default_factory=list)


def compile_method_candidate(
    method: LearnedMethod, instance_values: dict[str, str]
) -> LearnedMethod:
    """Remove run-instance text and repeated retries from a successful trace."""

    values = [normalize(str(value)) for value in instance_values.values() if str(value)]

    def instance_text(text: str) -> bool:
        candidate = normalize(text.replace("..", ""))
        prefix = normalize(text.split("..", 1)[0])
        return any(
            len(value) >= 4
            and (
                candidate == value
                or value in candidate
                or (".." in text and len(prefix) >= 4 and value.startswith(prefix))
            )
            for value in values
        )

    def redact(text: str) -> str:
        result = text
        for source, value in sorted(
            instance_values.items(), key=lambda item: len(str(item[1])), reverse=True
        ):
            if str(value):
                result = result.replace(str(value), "{" + source + "}")
        return result

    groups = []
    signatures = set()
    for group in method.action_groups:
        if (
            group.screen_phash is not None
            and group.image_size is not None
            and all(
                target.point is not None and target.bbox is not None for target in group.targets
            )
        ):
            compiled = group.model_copy(
                update={
                    "purpose": redact(group.purpose),
                    "expected_result": redact(group.expected_result),
                    "targets": [
                        target.model_copy(
                            update={"anchor_texts": [redact(text) for text in target.anchor_texts]}
                        )
                        if target.name in group.message_targets
                        else target
                        for target in group.targets
                    ],
                }
            )
            signature = json.dumps(
                {
                    "step": compiled.taught_step_id,
                    "screen_phash": compiled.screen_phash,
                    "image_size": compiled.image_size,
                    "targets": [
                        {
                            "role": target.role,
                            "point": target.point,
                            "bbox": target.bbox,
                            "message_text": target.anchor_texts
                            if target.name in compiled.message_targets
                            else [],
                        }
                        for target in compiled.targets
                    ],
                    "actions": [
                        {
                            "target": action.target,
                            "action": action.action,
                            "intent": action.intent,
                            "slot": action.slot,
                            "keys": action.keys,
                            "point": action.point,
                        }
                        for action in compiled.actions
                    ],
                    "save_result": compiled.save_result,
                    "repair_fields": compiled.repair_fields,
                    "observed_fields": [
                        target.model_dump(mode="json") for target in compiled.observed_fields
                    ],
                    "field_sources": compiled.field_sources,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            if signature in signatures:
                continue
            signatures.add(signature)
            groups.append(compiled)
    if not groups:
        raise ValueError("method_candidate_has_no_reusable_action_groups")
    completion = method.completion
    if completion is not None:
        targets = []
        for item in completion.targets:
            anchors = [text for text in item.target.anchor_texts if not instance_text(text)]
            if not anchors:
                completion = None
                break
            targets.append(
                item.model_copy(
                    update={"target": item.target.model_copy(update={"anchor_texts": anchors})}
                )
            )
        if completion is not None:
            completion = completion.model_copy(
                update={
                    "targets": targets,
                    "purpose": redact(completion.purpose),
                    "expected_result": redact(completion.expected_result),
                }
            )
    return method.model_copy(
        update={
            "purpose": redact(method.purpose),
            "action_groups": groups,
            "completion": completion,
        }
    )


@dataclass(frozen=True)
class BoundMethod:
    method_id: str
    group_id: str
    response: dict[str, Any]


def _relation(target: list | tuple, anchor: list | tuple) -> str:
    if contains(target, anchor):
        return "inside"
    x, y, w, h = target
    a, b, c, d = anchor
    dx, dy = x + w / 2 - a - c / 2, y + h / 2 - b - d / 2
    if abs(dx) / max(w, c) >= abs(dy) / max(h, d):
        return "right" if dx >= 0 else "left"
    return "below" if dy >= 0 else "above"


def _anchor_box(anchors: list[dict]) -> tuple[float, float, float, float]:
    left = min(item["bbox"][0] for item in anchors)
    top = min(item["bbox"][1] for item in anchors)
    right = max(item["bbox"][0] + item["bbox"][2] for item in anchors)
    bottom = max(item["bbox"][1] + item["bbox"][3] for item in anchors)
    return left, top, right - left, bottom - top


def _anchor_geometry(box: list | tuple, anchors: list[dict]) -> tuple[float, ...]:
    x, y, width, height = box
    left, top, anchor_width, anchor_height = _anchor_box(anchors)
    return (
        (x - left) / anchor_width,
        (y - top) / anchor_height,
        width / anchor_width,
        height / anchor_height,
    )


def _method_target(
    *,
    name: str,
    marker: dict,
    control: dict,
    blocks: dict[int, dict],
    allow_literal_anchor: bool,
) -> MethodTarget | None:
    anchor_ids = control.get("label_ids", [])
    if not anchor_ids and allow_literal_anchor and control["role"] not in {"edit", "combobox"}:
        anchor_ids = control.get("text_ids", []) or marker.get("text_ids", [])
    anchors = [blocks[i] for i in anchor_ids if i in blocks and blocks[i]["text"].strip()]
    if not anchors:
        return None
    relations = {_relation(marker["bbox"], item["bbox"]) for item in anchors}
    if len(relations) != 1:
        return None
    return MethodTarget(
        name=name,
        role=control["role"],
        anchor_texts=[item["text"] for item in anchors],
        relation=relations.pop(),
        marker_source=marker["source"],
        anchor_geometry=_anchor_geometry(marker["bbox"], anchors),
    )


def method_from_match(
    screen: dict,
    response: dict,
    *,
    method_id: str,
    purpose: str,
    slots: list[MethodSlot] | None = None,
    context_anchors: tuple[str, ...] | list[str] = (),
    source_transitions: tuple[str, ...] | list[str] = (),
    task_objective: str = "",
    task_contract_sha256: str = "",
    values: dict[str, str] | None = None,
) -> LearnedMethod | None:
    """Retain executed screen-bound coordinates and parameterize their input sources.

    The caller supplies model-extracted purpose and input roles. Coordinates, screenshot
    identity and source bindings come from executed actions, not a second reconstruction.
    """
    if screen.get("screen_phash") is None or not screen.get("image_size"):
        return None
    markers = {item["id"]: item for item in screen.get("markers", [])}
    controls = {item["id"]: item for item in response.get("controls", [])}
    steps = response.get("steps", [])
    if not steps or response.get("operation", "act") != "act":
        return None
    if slots is None:
        sources = dict.fromkeys(step["source"] for step in steps if step.get("source"))
        slots = [MethodSlot(name=key, description=key, source_key=key) for key in sources]
    by_source = {slot.source_key: slot.name for slot in slots}
    targets: dict[int, MethodTarget] = {}
    actions = []

    def coordinate_target(
        target_id: int, control: dict, point: list | tuple | None, default_name: str
    ) -> MethodTarget | None:
        marker = markers.get(target_id)
        selected = screen.get("selected_fields", {}).get(str(target_id), {})
        bbox = selected.get("bbox") or (marker["bbox"] if marker else None)
        if point is None:
            if bbox is None:
                return None
            x, y, width, height = marker["bbox"] if marker else bbox
            point = [x + width / 2, y + height / 2]
        return MethodTarget(
            name=selected.get("name") or default_name,
            role=control["role"],
            point=tuple(point),
            bbox=tuple(bbox or [int(point[0]), int(point[1]), 1, 1]),
        )

    for step in steps:
        control = controls.get(step["target"])
        if control is None:
            if step.get("point") is None or screen.get("screen_phash") is None:
                return None
            control = {"role": "edit" if step["action"] == "fill" else "button"}
        source = step.get("source")
        if source and source not in by_source:
            return None
        target_id = step["target"]
        if target_id not in targets:
            target = coordinate_target(
                target_id,
                control,
                step.get("point"),
                source or control.get("visual_description") or f"target_{len(targets) + 1}",
            )
            if target is None:
                return None
            targets[target_id] = target
        actions.append(
            MethodAction(
                target=targets[target_id].name,
                action=step["action"],
                intent=step["intent"],
                slot=by_source.get(source),
                keys=step.get("keys"),
                point=step.get("point") or targets[target_id].point,
            )
        )
    message_targets = []
    for target_id in dict.fromkeys(response.get("message_targets", [])):
        control = controls.get(target_id)
        if control is None:
            continue
        target = targets.get(target_id) or coordinate_target(
            target_id,
            control,
            None,
            control.get("visual_description") or f"message_{len(message_targets) + 1}",
        )
        if target is None:
            continue
        text = " ".join(
            block["text"]
            for block in sorted(screen.get("ocr", []), key=lambda b: (b["bbox"][1], b["bbox"][0]))
            if contains(target.bbox, block["bbox"])
        )
        if not text.strip():
            continue
        for source, value in sorted(
            (values or {}).items(), key=lambda item: len(str(item[1])), reverse=True
        ):
            if str(value):
                text = text.replace(str(value), "{" + source + "}")
        targets[target_id] = target.model_copy(update={"anchor_texts": [text]})
        message_targets.append(target.name)
    # Incomplete historical annotations leave an action-only method. Selection
    # still requests the current save review rather than inventing a result.
    has_save_review = bool(message_targets) and len(message_targets) == len(
        set(response.get("message_targets", []))
    )
    observed_fields = []
    field_sources = {}
    sources_by_target = {
        **{item["target"]: item["field"] for item in response.get("evidence", [])},
        **{step["target"]: step["source"] for step in steps if step.get("source")},
    }
    observed_controls = {
        **{
            int(ref): {"role": field["role"]}
            for ref, field in screen.get("selected_fields", {}).items()
        },
        **controls,
    }
    for target_id, control in observed_controls.items():
        name = (
            screen.get("selected_fields", {}).get(str(target_id), {}).get("name")
            or sources_by_target.get(target_id)
            or control.get("visual_description")
        )
        if target_id not in targets and not name:
            continue
        target = targets.get(target_id) or coordinate_target(target_id, control, None, name)
        if target is None:
            continue
        # Message anchors live on action targets. Never copy instance values into this layout.
        observed_fields.append(target.model_copy(update={"anchor_texts": []}))
        if source := sources_by_target.get(target_id) or screen.get("selected_fields", {}).get(
            str(target_id), {}
        ).get("source"):
            field_sources[target.name] = source
    group = MethodActionGroup(
        id="pending",
        purpose=response.get("purpose", "")[:160],
        taught_step_id=response.get("taught_step_id"),
        context_anchors=list(context_anchors),
        targets=list(targets.values()),
        actions=actions,
        expected_result=response.get("expected_result", "")[:240],
        screen_kind=response.get("screen_kind", "unknown"),
        screen_phash=screen.get("screen_phash"),
        image_size=screen.get("image_size"),
        observed_fields=observed_fields,
        field_sources=field_sources,
        save_result=response.get("save_result") if has_save_review else None,
        message_targets=message_targets if has_save_review else [],
        repair_fields=list(response.get("repair_fields", [])) if has_save_review else [],
    )
    encoded = json.dumps(group.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    group = group.model_copy(update={"id": hashlib.sha256(encoded.encode()).hexdigest()[:16]})
    return LearnedMethod(
        id=method_id,
        purpose=purpose,
        task_objective=task_objective,
        task_contract_sha256=task_contract_sha256,
        slots=slots,
        action_groups=[group],
        source_transitions=list(source_transitions),
    )


def method_completion_from_match(
    screen: dict, response: dict, method: LearnedMethod
) -> LearnedMethod | None:
    """Compile model-approved final proof into current-value-bound method evidence."""

    if response.get("operation") != "complete" or response.get("steps"):
        return None
    evidence = response.get("evidence", [])
    completion_evidence = response.get("completion_evidence", [])
    if not evidence or not completion_evidence:
        return None
    markers = {item["id"]: item for item in screen.get("markers", [])}
    blocks = {item["id"]: item for item in screen.get("ocr", [])}
    controls = {item["id"]: item for item in response.get("controls", [])}
    slots = list(method.slots)
    by_source = {slot.source_key: slot.name for slot in slots}
    for field in dict.fromkeys(item["field"] for item in evidence):
        if field not in by_source:
            by_source[field] = field
            slots.append(MethodSlot(name=field, description=field, source_key=field))
    conditions: dict[int, list[int]] = {}
    for item in completion_evidence:
        conditions.setdefault(item["target"], []).append(item["condition_index"])
    targets = []
    for index, item in enumerate(evidence, start=1):
        marker = markers.get(item["target"])
        control = controls.get(item["target"])
        condition_indexes = conditions.get(item["target"])
        if marker is None or control is None or not condition_indexes:
            return None
        target = _method_target(
            name=f"completion_{index}",
            marker=marker,
            control=control,
            blocks=blocks,
            # Literal values are instance data and can never locate later proof.
            allow_literal_anchor=False,
        )
        if target is None:
            return None
        targets.append(
            MethodCompletionTarget(
                slot=by_source[item["field"]],
                target=target,
                condition_indexes=list(dict.fromkeys(condition_indexes)),
            )
        )
    completion = MethodCompletion(
        purpose=response.get("purpose", "")[:160],
        taught_step_id=response.get("taught_step_id"),
        targets=targets,
        expected_result=response.get("expected_result", "")[:240],
        screen_kind=response.get("screen_kind", "unknown"),
    )
    return method.model_copy(update={"slots": slots, "completion": completion})


def _candidate_score(box: list | tuple, anchors: list[dict], relation: str) -> float | None:
    if any(_relation(box, anchor["bbox"]) != relation for anchor in anchors):
        return None
    x, y, w, h = box
    if relation == "inside":
        return float(w * h)
    score = 0.0
    for anchor in anchors:
        a, b, c, d = anchor["bbox"]
        if relation in {"left", "right"}:
            if min(y + h, b + d) <= max(y, b):
                return None
            distance = max(0, x - a - c) if relation == "right" else max(0, a - x - w)
            score += distance + abs(y + h / 2 - b - d / 2)
        else:
            if min(x + w, a + c) <= max(x, a):
                return None
            distance = max(0, y - b - d) if relation == "below" else max(0, b - y - h)
            score += distance + abs(x + w / 2 - a - c / 2)
    return score


def _bind_target(target: MethodTarget, screen: dict) -> tuple[dict, list[int]] | None:
    if target.point is not None:
        return None  # Group-level image match is required; coordinates alone are not value proof.
    matches = []
    for text in target.anchor_texts:
        found = [b for b in screen["ocr"] if normalize(b["text"]) == normalize(text)]
        if not found:
            return None
        matches.append(found)
    candidates = []
    for anchors in product(*matches):
        for marker in screen["markers"]:
            if not marker_role_allowed(marker["source"], target.role):
                continue
            if target.anchor_geometry is None:
                score = _candidate_score(marker["bbox"], anchors, target.relation)
            else:
                geometry = _anchor_geometry(marker["bbox"], anchors)
                learned = target.anchor_geometry
                # Compare the learned label-to-control relationship rather than
                # distance to a control's center (wide inputs have distant centers).
                score = sum(
                    abs(geometry[i] - learned[i]) / (1 + abs(learned[i])) for i in (0, 1)
                ) + sum(abs(log(geometry[i] / learned[i])) for i in (2, 3))
            if score is not None:
                candidates.append((score, marker, [anchor["id"] for anchor in anchors]))
    if not candidates:
        return None
    # Current labels locate the target; detector source only resolves equal geometry.
    candidates.sort(key=lambda item: (item[0], item[1]["source"] != target.marker_source))
    if target.anchor_geometry is not None and candidates[0][0] > MAX_RELATIVE_GEOMETRY_SCORE:
        return None
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        if (candidates[0][1]["source"] == target.marker_source) == (
            candidates[1][1]["source"] == target.marker_source
        ):
            return None
    return candidates[0][1], candidates[0][2]


def _marker_text_ids(marker: dict, screen: dict) -> list[int]:
    if marker["source"] == "ocr":
        return list(marker.get("text_ids", []))
    return [
        block["id"]
        for block in sorted(screen["ocr"], key=lambda b: (b["bbox"][1], b["bbox"][0]))
        if contains(marker["bbox"], block["bbox"])
    ]


def _bound_control(target: MethodTarget, marker: dict, label_ids: list[int], screen: dict) -> dict:
    return ControlMeaning(
        id=marker["id"],
        role=target.role,
        label_ids=label_ids,
        text_ids=_marker_text_ids(marker, screen),
        confidence=1,
        confidence_policy="argmax",
        visual_description=target.name,
    ).model_dump(mode="json")


def _register_bound_target(screen: dict, target: MethodTarget, marker: dict) -> None:
    """Keep action and evidence references in the same current-frame namespace."""
    markers = screen.setdefault("markers", [])
    if not any(item["id"] == marker["id"] for item in markers):
        markers.append({**marker, "source": "model_selected", "candidate_confidence": 1})
    screen.setdefault("selected_fields", {}).setdefault(
        str(marker["id"]),
        {"name": target.name, "bbox": list(target.bbox), "role": target.role},
    )


def screen_similarity_distance(
    group: MethodActionGroup, screen: dict, max_phash_distance: int
) -> int | None:
    """Use the same viewport/pHash contract before OCR and when binding actions."""
    current_hash = screen.get("screen_phash")
    if (
        group.screen_phash is None
        or current_hash is None
        or tuple(screen.get("image_size", ())) != group.image_size
    ):
        return None
    distance = hash_distance(group.screen_phash, current_hash)
    return distance if distance <= max_phash_distance else None


def _coordinate_bindings(
    group: MethodActionGroup,
    screen: dict,
    max_phash_distance: int,
    *,
    targets: list[MethodTarget] | None = None,
) -> dict[str, tuple[dict, list[int]]] | None:
    if screen_similarity_distance(group, screen, max_phash_distance) is None:
        return None
    targets = group.targets if targets is None else targets
    markers = {marker["id"]: marker for marker in screen.get("markers", [])}
    next_id = max(markers, default=0) + 1
    if any(target.point is None or target.bbox is None for target in targets):
        return None
    selected = {
        (tuple(field["bbox"]), field["role"]): markers[int(ref)]
        for ref, field in screen.get("selected_fields", {}).items()
        if int(ref) in markers
    }
    bound = {}
    for target in targets:
        marker = selected.get((target.bbox, target.role))
        if marker is None:
            marker = dict(id=next_id, source="learned_point", bbox=list(target.bbox), text_ids=[])
            next_id += 1
        bound[target.name] = (marker, [])
    return bound


def _bind_save_review(
    group: MethodActionGroup, controls: dict[int, dict], bound: dict, screen: dict, values: dict
) -> dict:
    """Reuse the learned classification only with its messages on this frame."""
    if group.save_result in {None, "unknown"} or not group.message_targets:
        return {}
    targets = {target.name: target for target in group.targets}
    blocks = {block["id"]: block["text"] for block in screen.get("ocr", [])}
    message_ids = []
    for name in group.message_targets:
        target = targets.get(name)
        if target is None or not target.anchor_texts:
            return {}
        marker, _ = bound[name]
        observed = " ".join(blocks[i] for i in controls[marker["id"]]["text_ids"])
        expected = " ".join(target.anchor_texts)
        for source, value in values.items():
            expected = expected.replace("{" + source + "}", str(value))
        if not observed.strip() or normalize(observed) != normalize(expected):
            return {}
        message_ids.append(marker["id"])
    return {
        "save_result": group.save_result,
        "message_targets": message_ids,
        "repair_fields": group.repair_fields,
    }


class MethodLibrary:
    def __init__(self, path: Path, *, enabled: bool = False, max_phash_distance: int = 6):
        self.path = Path(path)
        self.enabled = enabled
        self.max_phash_distance = max_phash_distance

    def methods(self) -> list[LearnedMethod]:
        if not self.enabled or not self.path.exists():
            return []
        return MethodDocument.model_validate_json(self.path.read_bytes()).methods

    def catalog(
        self, task: str | None = None, task_contract_sha256: str | None = None
    ) -> list[dict]:
        return [
            {
                "id": method.id,
                "purpose": method.purpose,
                "task_objective": method.task_objective,
                "slots": [slot.model_dump() for slot in method.slots],
                "groups": [
                    {
                        "id": group.id,
                        "purpose": group.purpose,
                        "taught_step_id": group.taught_step_id,
                        "trace_index": group.trace_index,
                        "predecessor_group_ids": group.predecessor_group_ids,
                        "context_anchors": group.context_anchors,
                    }
                    for group in method.action_groups
                ],
            }
            for method in self.methods()
            if task is None or not method.task_objective or method.task_objective == task
            if task_contract_sha256 is None or method.task_contract_sha256 == task_contract_sha256
        ]

    def upsert(
        self, method: LearnedMethod, *, replace_action_groups: bool = False
    ) -> LearnedMethod:
        if not self.enabled:
            return method
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(self.path.with_suffix(self.path.suffix + ".lock"), mode="a"):
            existing = {item.id: item for item in self.methods()}
            previous = existing.get(method.id)
            if previous is not None:
                if (
                    previous.task_contract_sha256 != method.task_contract_sha256
                    or previous.task_objective != method.task_objective
                ):
                    raise ValueError("method_id_task_scope_collision")
                # The incoming method carries the runtime's successful procedure
                # order. Runtime promotion may replace obsolete branches with one
                # fully verified path; import and authoring callers retain variants.
                groups = {item.id: item for item in method.action_groups}
                if not replace_action_groups:
                    for item in previous.action_groups:
                        groups.setdefault(item.id, item)
                method = method.model_copy(
                    update={
                        "revision": previous.revision + 1,
                        "action_groups": list(groups.values()),
                        "slots": list(
                            {slot.name: slot for slot in previous.slots + method.slots}.values()
                        ),
                        "source_transitions": list(
                            dict.fromkeys(previous.source_transitions + method.source_transitions)
                        ),
                        "completion": method.completion or previous.completion,
                    }
                )
            existing[method.id] = method
            atomic_write_json(self.path, MethodDocument(methods=list(existing.values())))
        return method

    def bind(
        self,
        method_id: str,
        slot_map: dict[str, str],
        screen: dict,
        values: dict[str, str],
        *,
        group_id: str | None = None,
    ) -> dict | None:
        bound = self.bind_with_context(method_id, slot_map, screen, values, group_id=group_id)
        return None if bound is None else bound.response

    def bind_with_context(
        self,
        method_id: str,
        slot_map: dict[str, str],
        screen: dict,
        values: dict[str, str],
        *,
        group_id: str | None = None,
    ) -> BoundMethod | None:
        method = next((item for item in self.methods() if item.id == method_id), None)
        if method is None:
            return None
        sources = {slot.name: slot_map.get(slot.name, slot.source_key) for slot in method.slots}
        for group in method.action_groups:
            if group_id is not None and group.id != group_id:
                continue
            current_kind = screen.get("screen_kind")
            if (
                current_kind
                and group.screen_kind != "unknown"
                and current_kind != group.screen_kind
            ):
                continue
            bound = _coordinate_bindings(group, screen, self.max_phash_distance)
            if bound is None:
                continue
            if len({value[0]["id"] for value in bound.values()}) != len(bound):
                continue
            steps, controls = [], {}
            for target in group.targets:
                marker, label_ids = bound[target.name]
                controls[marker["id"]] = _bound_control(target, marker, label_ids, screen)
            for action in group.actions:
                marker, _ = bound[action.target]
                source = sources.get(action.slot) if action.slot else None
                if action.slot and (source is None or source not in values):
                    break
                target = next(item for item in group.targets if item.name == action.target)
                chosen = action.point or target.point
                point = list(chosen) if chosen is not None else None
                if point is None:
                    break
                steps.append(
                    {
                        "target": marker["id"],
                        "action": action.action,
                        "intent": action.intent,
                        "source": source,
                        "keys": action.keys,
                        "point": point,
                    }
                )
            else:
                for target in group.targets:
                    marker, _ = bound[target.name]
                    _register_bound_target(screen, target, marker)
                return BoundMethod(
                    method.id,
                    group.id,
                    {
                        "screen_kind": group.screen_kind,
                        "controls": list(controls.values()),
                        "procedure_step": method.purpose,
                        "purpose": group.purpose or method.purpose,
                        "taught_step_id": group.taught_step_id,
                        "steps": steps,
                        "operation": "act",
                        "expected_result": group.expected_result,
                        **_bind_save_review(
                            group,
                            controls,
                            bound,
                            screen,
                            {
                                **values,
                                **{
                                    slot.source_key: values[sources[slot.name]]
                                    for slot in method.slots
                                    if sources[slot.name] in values
                                },
                            },
                        ),
                    },
                )
        return None

    def bind_visible_value_evidence(
        self,
        method_id: str,
        slot_map: dict[str, str],
        screen: dict,
        values: dict[str, str],
    ) -> tuple[list[dict], list[dict]]:
        """Match current OCR values at remembered input and passive-field locations."""

        method = next((item for item in self.methods() if item.id == method_id), None)
        if method is None:
            return [], []
        sources = {slot.name: slot_map.get(slot.name, slot.source_key) for slot in method.slots}
        blocks = {block["id"]: block for block in screen.get("ocr", [])}
        input_bindings = {slot.source_key: sources[slot.name] for slot in method.slots}
        controls: dict[int, dict] = {}
        evidence: dict[str, dict] = {}
        for group in method.action_groups:
            targets = {target.name: target for target in [*group.observed_fields, *group.targets]}
            bound = _coordinate_bindings(
                group, screen, self.max_phash_distance, targets=list(targets.values())
            )
            if bound is None:
                continue
            field_sources = {
                **{
                    name: input_bindings.get(source, source)
                    for name, source in group.field_sources.items()
                },
                **{
                    action.target: sources.get(action.slot)
                    for action in group.actions
                    if action.action == "fill" and action.slot is not None
                },
            }
            for name, source in field_sources.items():
                target = targets.get(name)
                if source is None or source not in values or target is None or source in evidence:
                    continue
                marker, label_ids = bound[target.name]
                text_ids = _marker_text_ids(marker, screen)
                visible = [blocks[text_id]["text"] for text_id in text_ids if text_id in blocks]
                if visible:
                    visible.append(" ".join(visible))
                if not any(field_matches(source, text, values) for text in visible):
                    continue
                _register_bound_target(screen, target, marker)
                controls[marker["id"]] = _bound_control(target, marker, label_ids, screen)
                evidence[source] = {"field": source, "target": marker["id"]}
        return list(controls.values()), list(evidence.values())

    def bind_completion_with_context(
        self,
        method_id: str,
        slot_map: dict[str, str],
        screen: dict,
        values: dict[str, str],
    ) -> dict | None:
        """Bind learned final proof and verify every current visible business value."""

        method = next((item for item in self.methods() if item.id == method_id), None)
        if method is None or method.completion is None:
            return None
        sources = {slot.name: slot_map.get(slot.name, slot.source_key) for slot in method.slots}
        blocks = {block["id"]: block for block in screen.get("ocr", [])}
        controls: dict[int, dict] = {}
        evidence = []
        completion_evidence = []
        for item in method.completion.targets:
            source = sources.get(item.slot)
            if source is None or source not in values:
                return None
            bound = _bind_target(item.target, screen)
            if bound is None:
                return None
            marker, label_ids = bound
            text_ids = _marker_text_ids(marker, screen)
            visible = [blocks[text_id]["text"] for text_id in text_ids if text_id in blocks]
            if visible:
                visible.append(" ".join(visible))
            if not any(field_matches(source, text, values) for text in visible):
                return None
            controls[marker["id"]] = ControlMeaning(
                id=marker["id"],
                role=item.target.role,
                label_ids=label_ids,
                text_ids=text_ids,
                confidence=1,
            ).model_dump(mode="json")
            evidence.append({"field": source, "target": marker["id"]})
            completion_evidence.extend(
                {"condition_index": condition_index, "target": marker["id"]}
                for condition_index in item.condition_indexes
            )
        completion = method.completion
        return {
            "screen_kind": completion.screen_kind,
            "controls": list(controls.values()),
            "procedure_step": "task-action",
            "purpose": completion.purpose or method.purpose,
            "taught_step_id": completion.taught_step_id,
            "steps": [],
            "operation": "complete",
            "evidence": evidence,
            "completion_evidence": completion_evidence,
            "expected_result": completion.expected_result,
        }
