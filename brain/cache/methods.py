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
from brain.visual.markers import marker_role_allowed
from brain.visual.scene import ControlMeaning, contains
from sawon_runtime.storage import atomic_write_json


class MethodSlot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    description: str
    source_key: str


class MethodTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    role: str
    anchor_texts: list[str] = Field(min_length=1)
    relation: Literal["inside", "right", "left", "below", "above"]
    marker_source: str | None = None
    anchor_geometry: tuple[float, float, float, float] | None = None


class MethodAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: str
    action: Literal["fill", "click", "double_click", "key"]
    intent: str
    slot: str | None = None
    keys: str | None = None
    relative_point: tuple[float, float] | None = None


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
        targets = []
        for target in group.targets:
            anchors = [text for text in target.anchor_texts if not instance_text(text)]
            if not anchors or not any(
                len(normalize(text)) >= 2 or not normalize(text).isdigit() for text in anchors
            ):
                break
            targets.append(target.model_copy(update={"anchor_texts": anchors}))
        else:
            compiled = group.model_copy(
                update={
                    "targets": targets,
                    "purpose": redact(group.purpose),
                    "expected_result": redact(group.expected_result),
                }
            )
            signature = json.dumps(
                {
                    "step": compiled.taught_step_id,
                    "targets": [
                        {
                            "role": target.role,
                            "anchors": target.anchor_texts,
                            "relation": target.relation,
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
                        }
                        for action in compiled.actions
                    ],
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
) -> LearnedMethod | None:
    """Retain a model's meaning; replace its observed handles with label relations.

    The caller supplies model-extracted purpose and input roles. Geometry and source
    bindings come from actual executed actions, not a second model reconstruction.
    """
    markers = {item["id"]: item for item in screen.get("markers", [])}
    blocks = {item["id"]: item for item in screen.get("ocr", [])}
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
    for step in steps:
        marker, control = markers.get(step["target"]), controls.get(step["target"])
        if marker is None or control is None:
            return None
        source = step.get("source")
        if source and source not in by_source:
            return None
        if marker["id"] not in targets:
            target = _method_target(
                name=f"target_{len(targets) + 1}",
                marker=marker,
                control=control,
                blocks=blocks,
                allow_literal_anchor=True,
            )
            if target is None:
                return None
            targets[marker["id"]] = target
        point = None
        if step["action"] in {"click", "double_click"}:
            if step.get("point") is None:
                return None
            x, y, w, h = marker["bbox"]
            point = ((step["point"][0] - x) / w, (step["point"][1] - y) / h)
            if not all(0 <= coordinate <= 1 for coordinate in point):
                return None
        actions.append(
            MethodAction(
                target=targets[marker["id"]].name,
                action=step["action"],
                intent=step["intent"],
                slot=by_source.get(source),
                keys=step.get("keys"),
                relative_point=point,
            )
        )
    group = MethodActionGroup(
        id="pending",
        purpose=response.get("purpose", "")[:160],
        taught_step_id=response.get("taught_step_id"),
        context_anchors=list(context_anchors),
        targets=list(targets.values()),
        actions=actions,
        expected_result=response.get("expected_result", "")[:240],
        screen_kind=response.get("screen_kind", "unknown"),
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
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        if (candidates[0][1]["source"] == target.marker_source) == (
            candidates[1][1]["source"] == target.marker_source
        ):
            return None
    return candidates[0][1], candidates[0][2]


def _marker_text_ids(marker: dict, screen: dict) -> list[int]:
    if marker["source"] == "ocr":
        return list(marker.get("text_ids", []))
    return [block["id"] for block in screen["ocr"] if contains(marker["bbox"], block["bbox"])]


class MethodLibrary:
    def __init__(self, path: Path, *, enabled: bool = False):
        self.path = Path(path)
        self.enabled = enabled

    def methods(self) -> list[LearnedMethod]:
        if not self.enabled or not self.path.exists():
            return []
        return MethodDocument.model_validate_json(self.path.read_bytes()).methods

    def catalog(self, task: str | None = None) -> list[dict]:
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
        visible_text = {normalize(block["text"]) for block in screen.get("ocr", [])}
        for group in method.action_groups:
            if group_id is not None and group.id != group_id:
                continue
            if any(
                not any(normalize(anchor) in text for text in visible_text)
                for anchor in group.context_anchors
            ):
                continue
            bound = {target.name: _bind_target(target, screen) for target in group.targets}
            if any(value is None for value in bound.values()):
                continue
            if len({value[0]["id"] for value in bound.values()}) != len(bound):
                continue
            steps, controls = [], {}
            for target in group.targets:
                marker, label_ids = bound[target.name]
                controls[marker["id"]] = ControlMeaning(
                    id=marker["id"],
                    role=target.role,
                    label_ids=label_ids,
                    text_ids=(
                        list(marker.get("text_ids", []))
                        if marker["source"] == "ocr"
                        else [b["id"] for b in screen["ocr"] if contains(marker["bbox"], b["bbox"])]
                    ),
                    confidence=1,
                ).model_dump(mode="json")
            for action in group.actions:
                marker, _ = bound[action.target]
                source = sources.get(action.slot) if action.slot else None
                if action.slot and (source is None or source not in values):
                    break
                point = None
                if action.relative_point is not None:
                    x, y, w, h = marker["bbox"]
                    point = [x + action.relative_point[0] * w, y + action.relative_point[1] * h]
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
                    },
                )
        return None

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
