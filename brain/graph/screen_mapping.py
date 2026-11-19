"""Named screen semantics select frame regions; the runtime owns their geometry."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from brain.input_binding import collected_input_sources
from brain.observe.values import field_matches
from brain.task_instruction import ObservedStepResult, TaskInstruction
from brain.visual.scene import contains
from brain.vocabulary import (
    ACTION_CONTROL_ROLES,
    SCROLL_DIRECTION_KEYS,
    ControlRole,
    KeyboardInput,
    MatchActionIntent,
    ScrollDirection,
    WindowActionIntent,
)
from models.schema import compact_provider_schema

from .work_memo import memo_prompt_context


class ScreenFieldSemantics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: ControlRole
    source: str | None = Field(
        default=None,
        description="Exact supplied input or requested result key; null for other controls",
    )
    value: str = ""
    enabled: bool = True
    parent: str | None = Field(
        default=None, description="Exact name of a visible containing field; null if absent"
    )
    focused: bool = False
    table_state: Literal["not_table", "empty", "rows", "uncertain"] | None = Field(
        default=None,
        description="Observed table contents",
    )


class ScreenField(ScreenFieldSemantics):
    bbox: list[int] = Field(
        min_length=4,
        max_length=4,
        description="Original image x,y,width,height of observation evidence, not a click boundary",
    )


class NamedTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(description="Descriptive action target; need not be declared in fields")
    intent: MatchActionIntent
    point: list[float] = Field(
        min_length=2, max_length=2, description="Your chosen original image click/input point"
    )


class NamedAction(NamedTarget):
    action: Literal["fill", "click", "double_click"]
    source: str | None = None


class NamedKeyAction(BaseModel):
    """A key may use current focus or an explicitly grounded target."""

    model_config = ConfigDict(extra="forbid")
    target: str | None = Field(
        default=None, description="Optional action target label; null to use current focus"
    )
    intent: MatchActionIntent
    point: list[float] | None = Field(default=None, min_length=2, max_length=2)
    action: Literal["key"]
    keys: KeyboardInput


class NamedScrollAction(NamedTarget):
    target: str = Field(description="Visible field naming the scroll context, not a click target")
    point: list[float] = Field(
        min_length=2,
        max_length=2,
        description="Original image point in the scrollable region; no preliminary click",
    )
    action: Literal["scroll"]
    direction: ScrollDirection


class NamedFocusAction(BaseModel):
    """Select an observed window without inventing screen geometry."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["focus_window"]
    handle: int = Field(gt=0)
    intent: WindowActionIntent


class NamedOpenAppAction(BaseModel):
    """Open a relay-registered application without supplying a path or command."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["open_app"]
    app_id: str = Field(min_length=1)
    intent: WindowActionIntent


WindowAction = NamedFocusAction | NamedOpenAppAction


class NamedCompletionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition_index: int
    target: str


class ScreenMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    screen_kind: Literal["login", "menu", "query", "editor", "partner", "dialog", "unknown"] = (
        "unknown"
    )
    screen_path: list[str] = Field(
        default_factory=list,
        description=(
            "Current semantic screen path: parent window, then active tab/subview. "
            "Reuse known paths; scrolling does not change the path. Empty means unidentified."
        ),
    )
    fields: dict[str, ScreenField] = Field(
        description="Visible semantic names, e.g. 수량, 포장정보/단위."
    )
    needs_refinement: bool = Field(
        default=False,
        description="Request local scanning when a required visible control is missing",
    )
    observed_step_results: list[ObservedStepResult] = Field(
        default_factory=list,
        description=(
            "Evaluate focused/delivered taught steps using current observations and accumulated "
            "observations in the screen tree"
        ),
    )
    steps: list[
        Annotated[
            NamedAction | NamedKeyAction | NamedScrollAction | WindowAction,
            Field(discriminator="action"),
        ]
    ] = Field(default_factory=list)
    completion_evidence: list[NamedCompletionEvidence] = Field(default_factory=list)
    operation: Literal["act", "duplicate_clear", "duplicate_found", "complete", "stop"] = "act"
    purpose: str = ""
    procedure_step: str = "task-action"
    taught_step_id: str | None = None
    method_id: str | None = None
    resume_group_id: str | None = Field(
        default=None,
        description=("First remembered group still needed from the current observed state."),
    )
    requested_instruction_ids: list[str] = Field(default_factory=list)
    expected_result: str = ""
    save_result: (
        Literal["rejected", "confirmation_required", "ready_for_query", "unknown"] | None
    ) = None
    message_targets: list[str] = Field(default_factory=list)
    repair_fields: list[str] = Field(default_factory=list)
    grid: str | None = None
    row: str | None = None

    @model_validator(mode="before")
    @classmethod
    def named_entries(cls, value):
        # Read historical mappings without restoring their unused duplicate evidence contract.
        if isinstance(value, dict) and "evidence" in value:
            value = {key: item for key, item in value.items() if key != "evidence"}
        if (
            cls is ScreenMapping
            and isinstance(value, dict)
            and isinstance(value.get("fields"), list)
        ):
            value = dict(value)
            value["fields"] = {
                entry["name"]: {k: v for k, v in entry.items() if k != "name"}
                for entry in value["fields"]
            }
        return value

    def reuse_names(self, nodes: Mapping[str, dict]) -> ScreenMapping:
        """Keep the first name of an unambiguous input in this screen node."""
        if not self.screen_path:
            return self
        known = nodes.get(json.dumps(self.screen_path, ensure_ascii=False), {}).get("fields", {})
        counts = Counter(field.source for field in self.fields.values())
        names = {}
        for name, field in self.fields.items():
            prior = known.get(field.source, {})
            canonical = prior.get("label", name)
            if (
                field.source
                and counts[field.source] == 1
                and canonical != name
                and canonical not in self.fields
            ):
                names[name] = canonical
        collisions = Counter(names.values())
        names = {name: canonical for name, canonical in names.items() if collisions[canonical] == 1}
        if not names:
            return self
        return self.model_copy(
            update={
                "fields": {
                    names.get(name, name): field.model_copy(
                        update={"parent": names.get(field.parent, field.parent)}
                    )
                    if field.parent in names
                    else field
                    for name, field in self.fields.items()
                },
                "steps": [
                    step.model_copy(update={"target": names.get(step.target, step.target)})
                    if not isinstance(step, WindowAction)
                    else step
                    for step in self.steps
                ],
                "completion_evidence": [
                    item.model_copy(update={"target": names.get(item.target, item.target)})
                    for item in self.completion_evidence
                ],
                "message_targets": [names.get(name, name) for name in self.message_targets],
                "repair_fields": [names.get(name, name) for name in self.repair_fields],
                "grid": names.get(self.grid, self.grid),
                "row": names.get(self.row, self.row),
            }
        )

    def _field_name(self, reference: str, step=None) -> str:
        if reference in self.fields:
            return reference
        matching_names = [name for name, field in self.fields.items() if field.source == reference]
        if len(matching_names) == 1:
            return matching_names[0]
        if step is not None:
            if step.point is not None and not isinstance(step, NamedScrollAction):
                x, y = step.point
                matching_names = [
                    name
                    for name, field in self.fields.items()
                    if field.enabled
                    and field.role in ACTION_CONTROL_ROLES[step.action]
                    and field.bbox[0] <= x < field.bbox[0] + field.bbox[2]
                    and field.bbox[1] <= y < field.bbox[1] + field.bbox[3]
                ]
                if len(matching_names) == 1:
                    return matching_names[0]
            return reference
        raise KeyError(reference)

    def execution_view(self) -> ScreenMapping:
        """Project action handles without adding observations to the original mapping."""
        fields = dict(self.fields)
        steps = []
        for step in self.steps:
            if isinstance(step, WindowAction) or step.target is None:
                steps.append(step)
                continue
            name = self._field_name(step.target, step)
            if name not in fields:
                if step.point is None:
                    steps.append(step.model_copy(update={"target": None}))
                    continue
                x, y = step.point
                fields[name] = ScreenField(
                    role="edit" if step.action == "fill" else "button",
                    bbox=[int(x), int(y), 1, 1],
                    source=step.source if step.action == "fill" else None,
                )
            data = {"target": name}
            if step.action == "fill":
                data["source"] = step.source or fields[name].source
            steps.append(step.model_copy(update=data))
        return self.model_copy(update={"fields": fields, "steps": steps})

    def local_payload(self, screen: dict, inputs: Mapping[str, str] | None = None) -> dict:
        """Project observations and independent action handles into the current executor."""
        view = self.execution_view()
        fields = view.fields
        markers = [m for m in screen.get("markers", []) if m["source"] != "model_selected"]
        next_id = max((m["id"] for m in markers), default=0) + 1
        ids = {name: next_id + i for i, name in enumerate(fields)}
        # Parent links enrich observation only; an undeclared parent must not erase it.
        parents = {name: ids.get(field.parent) for name, field in fields.items()}
        controls = []
        bindings = {name: field.source for name, field in self.fields.items() if field.source}
        # The model's current view replaces speculative geometry from a similar capture.
        screen["screen_path"] = list(self.screen_path)
        screen["markers"] = markers
        geometry = screen["selected_fields"] = {}
        for name, field in fields.items():
            target = ids[name]
            text_ids = [b["id"] for b in screen.get("ocr", []) if contains(field.bbox, b["bbox"])]
            controls.append(
                dict(
                    id=target,
                    role=field.role,
                    label_ids=[],
                    text_ids=text_ids,
                    confidence=1,
                    confidence_policy="argmax",
                    enabled=field.enabled,
                    focused=field.focused,
                    parent_id=parents[name],
                    visual_description=name,
                    table_state=field.table_state
                    if field.table_state is not None
                    else "rows"
                    if field.role == "table" and text_ids
                    else "not_table",
                )
            )
            screen.setdefault("markers", []).append(
                dict(
                    id=target,
                    source="model_selected",
                    bbox=field.bbox,
                    text_ids=[],
                    candidate_confidence=1,
                )
            )
            geometry[str(target)] = dict(name=name, **field.model_dump(exclude_unset=True))
        result = self.model_dump(
            exclude={
                "fields",
                "screen_path",
                "steps",
                "evidence",
                "completion_evidence",
                "message_targets",
                "grid",
                "row",
            }
        )
        result["controls"] = controls
        # The provider explanation is unrestricted; the local method label is a summary.
        # Keep the complete explanation in the saved semantic mapping.
        result["purpose"] = self.purpose[:160]
        result["steps"] = []
        for step in view.steps:
            action = step.model_dump()
            if isinstance(step, WindowAction):
                result["steps"].append(action)
                continue
            action["target"] = ids[step.target] if step.target is not None else None
            if isinstance(step, NamedScrollAction):
                action["action"] = "key"
                action["keys"] = SCROLL_DIRECTION_KEYS[action.pop("direction")]
            result["steps"].append(action)
        # Fields own input bindings and readings, exactly as in screen memory.
        # The optional evidence list cannot invent or override a field's source.
        result["evidence"] = [
            dict(field=source, target=ids[name])
            for name, source in bindings.items()
            if "value" in self.fields[name].model_fields_set
            and (
                inputs is None
                or (source in inputs and field_matches(source, self.fields[name].value, inputs))
            )
        ]

        # Optional annotations must not discard an otherwise executable plan.
        # Unresolved references contribute no evidence; completion remains separate.
        unresolved = []

        def annotation_target(reference: str, kind: str) -> int | None:
            try:
                return ids[self._field_name(reference)]
            except KeyError:
                unresolved.append({"kind": kind, "target": reference})
                return None

        result["completion_evidence"] = []
        for item in self.completion_evidence:
            target = annotation_target(item.target, "completion_evidence")
            if target is not None:
                result["completion_evidence"].append(dict(item.model_dump(), target=target))
        result["message_targets"] = []
        for name in self.message_targets:
            target = annotation_target(name, "message_targets")
            if target is not None:
                result["message_targets"].append(target)
        result["repair_fields"] = (
            [bindings.get(name, name) for name in self.repair_fields]
            if self.save_result == "rejected"
            else []
        )
        uses_result = self.operation in {"duplicate_clear", "duplicate_found", "complete"}
        result["grid"] = (
            annotation_target(self.grid, "grid") if uses_result and self.grid is not None else None
        )
        result["row"] = (
            annotation_target(self.row, "row") if uses_result and self.row is not None else None
        )
        screen["unresolved_annotations"] = unresolved
        return result


class NamedScreenField(ScreenFieldSemantics):
    name: str
    source: str | None = Field(
        default=None, description="Input key of the destination being observed; null otherwise"
    )
    result_key: str | None = Field(
        default=None, description="Requested business fact read from its source; null otherwise"
    )
    region_refs: list[str] = Field(
        min_length=1,
        description="Current screen region refs identifying this control or its visible evidence",
    )


class ScreenMappingWire(ScreenMapping):
    """Model semantics refer to current candidates without generating field rectangles."""

    fields: list[NamedScreenField]

    def resolve(
        self,
        projected_frame: dict,
        *,
        task: TaskInstruction | None = None,
        memo_labels: Mapping[str, str] | None = None,
    ) -> ScreenMapping:
        """Resolve this frame's geometry and the education's observation bindings."""
        regions = {
            region["ref"]: region["bbox"]
            for key in ("control_regions", "text_regions", "memory_fields")
            for region in projected_frame.get(key, [])
        }
        result_keys = set(task.result_keys) if task is not None else set()
        read_references = collected_input_sources(task) if task is not None else {}
        memo_sources = {label: key for key, label in (memo_labels or {}).items()}
        referenced_fields = {
            self.grid,
            self.row,
            *self.message_targets,
            *self.repair_fields,
            *(item.target for item in self.completion_evidence),
            *(step.target for step in self.steps if not isinstance(step, WindowAction)),
            *(field.parent for field in self.fields),
        }
        fields = {}
        for field in self.fields:
            try:
                boxes = [regions[ref] for ref in field.region_refs]
            except KeyError as error:
                if (
                    field.source is None
                    and field.result_key is None
                    and field.name not in referenced_fields
                ):
                    continue
                raise ValueError("screen_mapping_region_ref_unknown") from error
            left = min(box[0] for box in boxes)
            top = min(box[1] for box in boxes)
            right = max(box[0] + box[2] for box in boxes)
            bottom = max(box[1] + box[3] for box in boxes)
            attributes = field.model_dump(
                exclude={"name", "region_refs", "result_key"}, exclude_unset=True
            )
            if field.result_key is not None:
                key = field.result_key
                reading = key if key in result_keys else read_references.get(key)
                attributes["source"] = reading if field.source is None else None
            elif memo_sources and field.source is not None:
                attributes["source"] = memo_sources.get(field.source)
            elif "result_key" in field.model_fields_set and field.source in result_keys:
                # A destination observation cannot stand in for an original reading.
                # Archived provider replies predate result_key and used source for both roles.
                attributes["source"] = None
            fields[field.name] = {
                **attributes,
                "bbox": [left, top, right - left, bottom - top],
            }
        payload = self.model_dump(exclude={"fields"}, exclude_unset=True)
        if memo_sources:
            for action in payload.get("steps", []):
                if action.get("source") is not None:
                    action["source"] = memo_sources.get(action["source"])
            if self.repair_fields:
                payload["repair_fields"] = [memo_sources.get(s, s) for s in self.repair_fields]
        return ScreenMapping.model_validate({**payload, "fields": fields})

    anthropic_unions: ClassVar[dict[str, dict[str, Any]]] = {
        "properties/steps/items": {
            "source": "",
            "keys": "",
            "direction": "",
            "target": "",
            "point": [],
            "handle": 0,
            "app_id": "",
        },
    }
    anthropic_nulls: ClassVar[dict[str, Any]] = {
        "properties/taught_step_id": "",
        "properties/method_id": "",
        "properties/resume_group_id": "",
        "properties/grid": "",
        "properties/row": "",
        "properties/save_result": "",
        "$defs/NamedAction/properties/source": "",
        "$defs/NamedKeyAction/properties/target": "",
        "$defs/NamedKeyAction/properties/point": [],
        "$defs/NamedScreenField/properties/source": "",
        "$defs/NamedScreenField/properties/result_key": "",
        "$defs/NamedScreenField/properties/parent": "",
        "$defs/NamedScreenField/properties/table_state": "",
    }

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = compact_provider_schema(super().model_json_schema(*args, **kwargs))
        # Current replies distinguish an explicit null from archived source-only observations.
        schema["$defs"]["NamedScreenField"]["required"].append("result_key")
        return schema


def mapping_prompt(
    context: dict, *, memo_labels: dict[str, str] | None = None, memo_title: str = ""
) -> str:
    if memo_labels:
        context = memo_prompt_context(context, memo_labels, memo_title)
        return WORK_MEMO_MAPPING_RULES + json.dumps(context, ensure_ascii=False)
    return CONTEXT_MAPPING_RULES + json.dumps(context, ensure_ascii=False)


def observation_prompt(projected_frame: dict) -> str:
    """Use the same named observation contract for recorders and observation-only runs."""
    return (
        "Describe this screen as ScreenMapping JSON with screen_path and named fields. "
        "Each field has a role, region_refs and a read value. control_regions contain detected "
        "controls and their OCR; text_regions contain OCR text. region_refs select these regions "
        "and the runtime supplies their geometry. Several regions can represent a wrapped value. "
        "parent names a containing field, focused describes input focus, and table_state describes "
        "table contents as empty, rows or uncertain. This observation uses result_key=null, "
        "source=null, steps=[] and operation=act. "
        "needs_refinement requests additional local scanning.\n"
        + json.dumps({"screen": projected_frame}, ensure_ascii=False)
    )


_MAPPING_TEMPLATE = """Plan GUI work from the user's task, education, screen and notebook.
Return ScreenMapping JSON containing observations and the next decision.

Input data:
screen provides image_size, OCR text_regions, control_regions and optional memory_fields.
screen.active_window identifies the captured window; screen.windows lists window handles and
titles. screen.available_apps provides registered app IDs and names.
{input_guidance}
notebook.screen_tree organizes accumulated fields and controls by screen_path and child views.
last_observation records a reading; delivery records an acknowledged input. observed_properties
contains table_state, enabled, focused and parent facts. Each fact has an observation_number;
notebook.observation_number identifies this capture. remaining_inputs lists keys awaiting
verification, and pending_verification_inputs lists deliveries awaiting a later reading.
execution.last_delivered_actions contains acknowledged actions.
notebook.recent_feedback[].execution_result contains delivered actions, the failed request and
the relay response; attempted records whether input dispatch started.
execution.last_action_observation describes structural screen movement.
taught_procedure_progress contains education details and progress. requested_instruction_ids
retrieves further procedure details. method_memory contains remembered procedures; method_id
selects a method and resume_group_id selects its next group.

Response data:
screen_path names the window and tab/subview. For each named field, value is the current screen
reading. Choose its link according to what the observed region represents:
- A fact in the requested material: result_key is the matching task.result_keys entry, source
  is null. This is the original source key in the education. Return the full value read for
  this task; the runtime stores it in notebook.collected_result with its screen provenance.
- A destination showing its current contents: {destination_guidance}
- Other visible controls and unrelated material: both links are null.
Several regions may describe one wrapped business value.
region_refs lists refs from control_regions, text_regions or memory_fields; the runtime derives bbox
from them. Action points use original image pixels independently of field evidence geometry.
The runtime merges returned fields and observed_step_results into the screen tree before
executing the steps. An explicit empty value updates a field; omission retains its prior reading.
observed_step_results records education progress with step_id, observed_result and completed.
completion_evidence, message_targets, grid and row reference named fields.
steps is an ordered action list. Each action has an intent describing its business purpose:
- fill uses target, point and source; the executor supplies the source's value.
- click and double_click use target and point.
- key uses keys and an optional target/point; null target/point uses current keyboard focus.
- scroll uses target as context, point as the wheel location and direction=up/down.
- focus_window uses a screen.windows handle and intent=navigate.
- open_app uses a screen.available_apps app_id and intent=navigate.
Window/app transitions use a single step and produce a new capture for the next decision.
needs_refinement requests additional local scanning. operation describes act, complete or stop;
taught_step_id identifies the education step associated with the decision.
Explain the decision rationale in Korean in purpose. expected_result describes the intended
result, and observed_result describes the facts relevant to the education outcome.
"""

CONTEXT_MAPPING_RULES = _MAPPING_TEMPLATE.format(
    input_guidance=(
        "notebook.inputs contains the available business inputs; available_credentials lists "
        "credential sources. task.result_keys names facts to collect. notebook.collected_result "
        "contains their readings, provenance and missing_keys."
    ),
    destination_guidance=(
        "source is the exact input named in the education; result_key is null. "
        "The fill action uses the same input."
    ),
)

WORK_MEMO_MAPPING_RULES = _MAPPING_TEMPLATE.format(
    input_guidance=(
        "The central planner selected notebook.work_memo for this job. Its items are named "
        "business facts and their current values. Match these items to visible fields using "
        "their meaning. Login uses the names listed in available_credentials."
    ),
    destination_guidance=(
        "source is the displayed item name from the selected memo (or the taught credential "
        "or current reading); result_key is null. A fill can simply target this field by its "
        "exact name with source=null. For a fill without a linked field, give the item name "
        "in the step's source. The runtime supplies its value."
    ),
)
