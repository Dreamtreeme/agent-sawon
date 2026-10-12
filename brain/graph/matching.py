"""Match the current OCR form to user task instructions and compile local actions."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from brain.cache import VisualMatchCache, visual_match_key
from brain.input_binding import (
    MODEL_INPUTS_NAME,
    allowed_input_surfaces,
    normalize_action_source,
    resolve_input_key,
)
from brain.observe.evidence import input_surface
from brain.settings import BrainSettings
from brain.task_instruction import TaskInstruction
from brain.visual.marker_contract import (
    allowed_marker_actions,
    local_response,
    model_controls,
    model_screen,
)
from brain.visual.markers import marker_role_allowed
from brain.visual.scene import ControlMeaning, SemanticDetection, contains, overlap
from brain.vocabulary import KeyInput
from models.base import model_call_context
from models.schema import compact_provider_schema
from sawon_runtime.storage import RunLog, atomic_write_json
from sawon_runtime.visual import RenderedPage

from .execution import GuardStop, PendingAction
from .model_call import ResponseT, ScreenModelCall
from .policy import DecisionPolicy
from .schemas import Choice, Decision, FieldName
from .visual_turn import adapt_proposal, bind_proposal


class ActionCapability(BaseModel):
    """The one runtime action envelope for the current user task."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    capability_id: Literal["task-action"] = "task-action"
    input_scope: list[FieldName] = Field(default_factory=list)
    actions: list[
        Literal[
            "login",
            "navigate",
            "query_input",
            "fill",
            "search",
            "save",
            "confirm_save",
            "select_tab",
            "select_option",
            "cancel",
            "wait",
            "complete",
            "inspect",
        ]
    ] = Field(default_factory=list)
    goal: str = Field(min_length=1, max_length=500)
    completion: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def valid_field_contract(self):
        if len(set(self.input_scope)) != len(self.input_scope):
            raise ValueError("duplicate_capability_input_scope")
        return self


class MatchAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: int = Field(ge=0)
    action: Literal["fill", "click", "double_click", "key"]
    source: str | None = Field(default=None, min_length=1, max_length=4000)
    intent: Literal[
        "credential",
        "login",
        "navigate",
        "query_input",
        "search",
        "fill",
        "save",
        "confirm_save",
        "select_tab",
        "select_option",
        "cancel",
    ]
    keys: KeyInput | None = None
    point: list[float] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description=(
            "Original-image [x,y] point inside target. Required for click/double_click and for "
            "every action whose target is a model-created control without a local marker."
        ),
    )

    @field_validator("keys", mode="before")
    @classmethod
    def normalize_keys(cls, value):
        """Canonicalize provider casing before compiling to the wire action."""
        if not isinstance(value, str):
            return value
        return value.strip().casefold().replace(" ", "").replace("_", "")

    @model_validator(mode="after")
    def explicit_visual_click_point(self):
        if self.action == "key" and self.keys is None:
            raise ValueError("key_action_requires_keys")
        if self.point is not None and any(value < 0 for value in self.point):
            raise ValueError("visual_click_point_negative")
        return self


class TargetedMatchAction(MatchAction):
    """Provider action owns its chosen control; runtime actions retain integer IDs."""

    target: ControlMeaning


class MatchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: FieldName
    target: int = Field(ge=0)


class MatchCompletionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition_index: int = Field(ge=0)
    target: int = Field(ge=0)


class ActionCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["act", "stop"]
    steps: list[MatchAction] = Field(max_length=50)

    @model_validator(mode="after")
    def executable_or_stop(self):
        if (self.operation == "act") != bool(self.steps):
            raise ValueError("act_requires_steps_stop_requires_none")
        return self


class MethodBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: str
    source: str


class MethodChoice(BaseModel):
    """Business purpose and a reusable method selected by the screen worker."""

    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(default="", max_length=160)
    taught_step_id: str | None = Field(default=None, max_length=80)
    method_id: str | None = None
    method_bindings: list[MethodBinding] = Field(default_factory=list, max_length=100)


class ScreenPlan(MethodChoice):
    """Task decision over a previously grounded screen interpretation."""

    anthropic_nulls: ClassVar[dict[str, Any]] = {
        "properties/taught_step_id": "",
        "properties/method_id": "",
        "properties/grid": -1,
        "properties/row": -1,
        "properties/save_result": "",
        "$defs/MatchAction/properties/source": "",
        "$defs/MatchAction/properties/keys": "",
        "$defs/MatchAction/properties/point": [],
    }

    model_config = ConfigDict(extra="forbid")
    procedure_step: str
    steps: list[MatchAction] = Field(default_factory=list, max_length=50)
    requested_instruction_ids: list[str] = Field(default_factory=list, max_length=40)
    operation: Literal["act", "duplicate_clear", "duplicate_found", "complete", "stop"] = "act"
    grid: int | None = Field(default=None, ge=0)
    row: int | None = Field(default=None, ge=0)
    evidence: list[MatchEvidence] = Field(default_factory=list, max_length=100)
    completion_evidence: list[MatchCompletionEvidence] = Field(default_factory=list, max_length=50)
    save_result: (
        Literal["rejected", "confirmation_required", "ready_for_query", "unknown"] | None
    ) = None
    message_targets: list[int] = Field(default_factory=list, max_length=50)
    repair_fields: list[FieldName] = Field(default_factory=list, max_length=100)
    expected_result: str = Field(default="", max_length=240)


class ScreenMatch(SemanticDetection, ScreenPlan):
    """Task plan with the same fields, plus current-screen interpretation."""

    anthropic_nulls: ClassVar[dict[str, Any]] = {
        **ScreenPlan.anthropic_nulls,
        **SemanticDetection.anthropic_nulls,
    }

    evidence: list[MatchEvidence] = Field(
        default_factory=list,
        max_length=100,
        description="Current field observations whose target is defined in controls "
        "or steps[].target. "
        "Omit past or incidental observations not needed by this action. Completion maps these to "
        "final row proof; duplicate checks verify the query input separately.",
    )
    needs_image: bool = False

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = super().model_json_schema(*args, **kwargs)
        if kwargs.get("mode", "validation") == "serialization":
            return schema
        # Only the provider input shape changes. Cache/artifact serialization and
        # all execution compilers retain the established ID-based contract.
        action_schema = TargetedMatchAction.model_json_schema()
        schema["$defs"].update(action_schema.pop("$defs", {}))
        schema["$defs"]["MatchAction"] = action_schema
        return compact_provider_schema(schema)

    @model_validator(mode="before")
    @classmethod
    def collect_action_bindings(cls, value):
        if not isinstance(value, dict):
            return value
        steps = value.get("steps", [])
        if not any(
            isinstance(step, dict) and isinstance(step.get("target"), dict) for step in steps
        ):
            return value
        controls = {}
        for raw in value.get("controls", []):
            control = ControlMeaning.model_validate(raw)
            controls[control.id] = control
        normalized = []
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("target"), dict):
                normalized.append(step)
                continue
            bound = TargetedMatchAction.model_validate(step)
            control = bound.target
            # The action owns its target binding.  ``controls`` may repeat the
            # same observed element as supporting evidence with different
            # descriptive metadata; that must not reject an executable action.
            controls[control.id] = control
            normalized.append({**bound.model_dump(), "target": control.id})
        return {**value, "controls": list(controls.values()), "steps": normalized}

    def interpretation(self) -> SemanticDetection:
        return SemanticDetection.model_validate(
            self.model_dump(include=set(SemanticDetection.model_fields))
        )


class MatchMethodRuntime(Protocol):
    """Method operations required by matching, independent of their implementation."""

    enabled: bool

    def prompt_context(self) -> dict: ...

    def apply(self, screen: dict) -> ScreenMatch | None: ...

    def remember(self, screen: dict, response: ScreenMatch) -> None: ...

    def reject_pending(self, *, reason: str) -> dict | None: ...


@dataclass(frozen=True)
class MatchRequest:
    """Immutable state needed to choose one action boundary for one screen."""

    screen: dict
    page: Any
    phase: str
    values: Mapping[str, str]
    verified_fields: frozenset[str]
    execution_contract: dict
    required_verification: tuple[dict, ...]
    post_action_review: bool
    pending_verification: bool
    workflow_flags: Mapping[str, bool]
    context_layers: dict
    recent_feedback: tuple[dict, ...]
    available_credentials: tuple[str, ...]
    evidence_claims: tuple[dict, ...]
    failed_transitions: tuple[dict, ...]
    saved_record_review: dict | None
    taught_procedure_progress: dict
    task_instruction: TaskInstruction
    task_instruction_sha256: str
    policy: DecisionPolicy
    grounded_interpretation: SemanticDetection | None = None
    proposal: ScreenMatch | None = None


@dataclass(frozen=True)
class MatchServices:
    """Explicit side-effect boundary used by screen matching."""

    settings: BrainSettings
    log: RunLog
    call: ScreenModelCall
    case_dir: Path
    cache: VisualMatchCache
    methods: MatchMethodRuntime


@dataclass(frozen=True)
class MatchResult:
    """Screen decision plus the two state updates owned by the graph runtime."""

    response: ScreenMatch
    visual_match_candidate: tuple[str, dict] | None
    observation_claim: dict | None = None


@dataclass(frozen=True)
class MatchPromptContext:
    state: dict
    capability: ActionCapability
    active_fields: frozenset[str]
    prompt: str
    guard_rules: str
    taught_rules: str
    method_rules: str


@dataclass(frozen=True)
class MatchSelection:
    response: ScreenMatch
    cache_hit: tuple[str, dict] | None
    method_proposal: ScreenMatch | None
    used_image: bool


def _call_match(
    services: MatchServices,
    purpose: str,
    prompt: str,
    schema: type[ResponseT],
    *,
    page: RenderedPage | None = None,
) -> ResponseT:
    with model_call_context(purpose=purpose):
        return services.call(prompt, schema, page=page)


def write_match_artifact(services: MatchServices, kind: str, value: BaseModel) -> None:
    atomic_write_json(services.case_dir / "matching" / f"{uuid4().hex}-{kind}.json", value)


SCREEN_BOUNDARY_BATCH_RULES = """
Return exactly one CURRENT-screen action boundary. A login boundary is one atomic batch: fill
every remaining credential source into its grounded input, then click the grounded login command
as the final step. Credential fills without that final login click are incomplete. Other valid
batches are direct business fills only, same-intent navigation only, or consecutive option-row and
confirmation clicks. Do not mix unrelated boundaries.
"""


TASK_INPUT_RULES = """
Distinguish field captions and column headers from the editable areas they name.
Match values to the data scope requested by the user (record or line item), not merely to a
same-text label in another section. Ground an input in its actual visible editable area.
Never classify a rectangle containing multiple smaller closed rectangles as an edit/combobox;
it is a container or grid candidate. When an input boundary is clipped at the viewport edge,
scroll to expose that input instead of borrowing a nearby table or container rectangle.
An OCR input marker must enclose that input's own current literal value, never its nearby label.
Treat OCR primarily as text evidence. A step target MUST name a marker whose allowed_actions
contains the requested action. An OCR marker may be a click target when its visible text is itself
the tab, button, menu, link, row, or cell to activate; it is never a fill or keyboard target.
"""


TASK_PLAN_RULES = (
    """Use the supplied grounded screen interpretation as immutable evidence.
Use the user task description, input data and runtime notebook to choose the next action from the
execution contract. The task description is business intent, not a UI route. Do not assume labels,
menus, tabs, coordinates or screen order that are absent from the current interpretation. Return one
screen-boundary batch and do not mix direct fills with navigation or selection. For complete, map
CURRENT visible proof to the applicable zero-based user completion condition in completion_evidence;
map visible business values separately in evidence. notebook.inputs is the canonical input map:
every data-bound step.source and evidence.field MUST be one exact key from notebook.inputs, without
record./inputs. prefixes and never a literal value. Use intent=fill for every business text input,
including filters and lookups. Treat a visible status or result as current evidence: when it already
shows a command's expected effect, do not repeat that command; advance the task from that result.
When a remaining input caption is visible but its writable boundary is clipped or absent at a
scrolling viewport edge, expose that boundary with grounded scrolling before stopping. Do not
reselect a current tab to expose a caption that is already visible. A merged OCR marker containing
multiple tab captions does not identify an inactive tab; visible panel content is stronger proof.
Return only task-plan schema fields and never reclassify controls or invent IDs.
Prefer grounded_interpretation.controls. When a necessary CURRENT action target exists in
screen.markers but was omitted from grounded_interpretation.controls, you may reference that real
marker ID; the runtime will ground only that target before execution. Omit incidental past-row
evidence that is not needed for this CURRENT action or completion decision.
"""
    + TASK_INPUT_RULES
    + SCREEN_BOUNDARY_BATCH_RULES
)


OBSERVE_ONLY_TARGET_RULES = """
Experimental model-trust mode is active. The current marker catalog is useful evidence, but it is
not an exhaustive list of visible click targets. When a clearly visible control needed for the
next action has no standalone marker because OCR merged adjacent labels or local detection omitted
it, you may assign a new nonnegative control ID and use it consistently in controls and steps.
For that control, provide its visible role, confidence_policy=argmax, and a precise
visual_description.
For EVERY action on a new control ID, provide an original-image point inside the intended visible
control. This includes fill and keyboard actions on a clearly visible blank edit field; the point
is how the runtime binds your semantic control to CURRENT-screen geometry.
This is an explicit exception to the preceding no-invented-ID rule. Do not use the exception for
hidden controls or targets outside the CURRENT image.
"""

TARGET_GROUNDING_RULES = """Ground only the listed action target markers on the CURRENT image.
The existing task plan is immutable: do not replace, reorder, or add actions.
Return each required_target_id exactly once in controls, plus only a necessary visible parent.
Use the supplied existing marker ID and geometry; never substitute an OCR caption marker for an
enclosing detector marker. Classify its visible role, labels, contained literal text, enabled state,
and focus state. All label_ids/text_ids use the printed visible text-marker IDs. Do not invent IDs,
boxes, values, actions, or hidden controls. Report uncertainty when the role cannot be grounded.
The requested action must appear in that marker's allowed_actions. OCR click proxies may retain
role=text when their visible caption is the requested click target; they cannot be writable or
keyboard targets.
"""


TASK_RULES = (
    """Use the user task description, input data, runtime notebook and CURRENT screen.
The task description contains business intent and completion conditions, not a fixed UI route.
Choose the next grounded action that advances the objective from the current evidence. Decide
whether to fill visible inputs, use a visible lookup/selection control, navigate, save, or verify.
Never assume a label, coordinate, menu, tab, screen order, or application-specific route that is
not visible now. notebook.inputs is the only business input map. Every data-bound step.source and
evidence.field MUST be one exact key from notebook.inputs, without record./inputs. prefixes and
never a literal value. Use intent=fill for every business text input, including filters and lookups.
Use only supplied input keys and current marker IDs. available_inputs is the reusable source list.
remaining_inputs contains values that have not yet been evidenced on any observed screen; it is
progress context, not a command to overwrite a matching CURRENT value. Before planning actions,
compare visible values with notebook.inputs. Put every matching direct input in evidence and do
not fill it again, even when it was previously listed in remaining_inputs.
When a remaining input caption is visible but its writable boundary is clipped or absent at a
scrolling viewport edge, ground a current control in that viewport and use scrolling to expose the
whole input before stopping. Do not reselect a current tab to expose a caption already visible.
A merged OCR marker containing multiple tab captions does not identify an inactive tab; use the
visible panel content as stronger evidence of the current view.
Define each steps[].target as the selected control object (id, role, OCR links, confidence).
Use controls only for additional evidence/parent controls; evidence may reference action target
IDs. Preserve the exact original-image action point in steps[].point. During operation=act,
omit past-row evidence unrelated to the current action.
In generic task mode those inputs remain reusable across screens: verified_inputs reports prior
evidence but does not mean a value is already present in a different current form.
When notebook.workflow_state.save_issued is true, the run is verifying the record that it just
saved. A matching row on that post-save query is the expected saved record, not a pre-entry
duplicate. Continue the user-taught post-save review instead of returning duplicate_found or stop.
When notebook.workflow_state.post_save_detail_review is true, the saved result row has already
been opened and verified_inputs contains only fields re-read from that persisted detail. Review
the saved record; do not fill or save again. Navigate only to expose remaining_inputs. Once no
remaining inputs exist, use CURRENT visible evidence for the applicable completion condition and
return operation=complete instead of revisiting a previously reviewed tab.
The execution contract is the complete list of actions currently allowed by code. Return one
screen-boundary action batch: direct fills only, login credentials plus login, same-intent
navigation clicks, or consecutive select_option row/confirmation clicks. Do not mix fills with
navigation or selection. A
field-bound lookup navigation must map the current field input in evidence so its selected value
can be checked on return. Complete only when CURRENT visible evidence supports an applicable
user-authored completion condition. Map each supporting marker to its zero-based condition_index
in completion_evidence; map visible business values separately in evidence. Follow all constraints
and never transmit or approve. Treat a visible status or result as current evidence: when it already
shows a command's expected effect, do not repeat that command; advance the task from that result.
operation=act MUST contain at least one executable step in this response. Never return an empty
act for a later completion call. In generic task phase, do not return duplicate_clear or
duplicate_found: record the visible query evidence and choose the next taught action in the same
response.
If a required input is not visible, choose an untried CURRENT affordance whose expected effect can
expose it. failed_transitions lists actions that already produced no change or a cycle for this same
screen state and goal gap; do not repeat them. Incomplete work alone is not a terminal reason to
stop while another grounded transition can expose the missing input or completion evidence.
"""
    + TASK_INPUT_RULES
)

TAUGHT_PROCEDURE_RULES = """
taught_procedure_progress.index lists every available user instruction. The detailed steps are
focused context, not a restriction on navigation or proof of completion. Revisit an earlier step
when the current screen requires it. If details for another step are needed, return its ID in
requested_instruction_ids with steps=[]; the runtime supplies those instructions before any action.
Request all needed IDs together. Do not infer that preceding steps completed from a selected ID.
The user-taught procedure is the authoritative program route. Select taught_step_id from that
ordered procedure. Use the current screen to bind that step's target and verify its completion;
do not replace the taught route with another workflow. Repeat a step when it requires several
screen-boundary actions. Advance only when current evidence or the just-delivered action supports
the preceding completion. If required content is below the current viewport, use grounded
navigation such as pagedown rather than reporting a missing detector control. operation=complete
is allowed only for the final taught step and its completion evidence.
Each taught step's actions preserve the user's actual button, tab and field instructions.
Treat target as a semantic CURRENT-screen label, never as a stored coordinate. For fill actions,
input_key identifies the value the user associated with that target. The procedure describes the
expected route. Fill only inputs associated with the selected taught step; select the appropriate
step when another taught group is visible. Perform taught navigation actions in order across
as many screen-boundary batches as needed; use exploration only when a taught target cannot be
grounded on the current screen.
For a fill action, surface=null means a standalone direct input. surface=table_cell explicitly
authorizes a grid cell and surface=any authorizes either. Do not bind a direct_input to a line-item
or detail-table cell merely because its column caption resembles the business input name.
For every fill and field-evidence binding, match the CURRENT control's printed label to the target
text paired with that exact input_key in taught_procedure. Never assign a different remaining input
to a visible field merely because that value has not been evidenced yet.
After saving, opening a query result must target the current row or cell carrying the literal
notebook.inputs value named by task.identity_input_key. A query caption or input control is not the
saved result row.
"""

METHOD_RULES = """
method_memory contains learned ways to achieve purposes, with parameter slots and observed
screen branches. Use relevant methods as task know-how. Return purpose as the specific business
subgoal of this batch, preserving it until achieved. When a method fits, return its method_id
and method_bindings mapping its slots to exact current notebook.inputs keys. Keep these meanings
across changed labels/layouts. If this screen needs a different way to perform that purpose,
return the appropriate CURRENT steps and controls with the same method_id and bindings: the
runtime will retain this observed variant for subsequent use. Choose steps from current evidence,
not the historical marker numbers. Current execution/results in method_memory are this run's
progress, not business completion proof. A null method_id means a new purpose or no suitable method.
For navigation into the screen that will perform a learned method, select that method_id and its
bindings NOW so the next observed screen can use it directly. Select only for the current or
immediately next subgoal; after a delivered batch the next purpose is chosen from new evidence.
"""

POST_ACTION_REVIEW_RULES = """
This call reviews the result of a field batch that was already executed. Describe the current
screen and map every readable supplied input to evidence. In the SAME response, choose the next
grounded action that advances the task when one is visible. Do not repeat a delivered field input.
Return completion only when current evidence supports it, and stop only when no grounded action or
supported completion is available. Review and next-action planning are one decision boundary; do
not defer the action to a later model call.
"""


def verification_requirements(
    controls, screen: dict, required: list[dict], screen_kind: str, threshold: float
):
    relevant = [r for r in required if r["screen"] == screen_kind]
    blocks = {b["id"]: b for b in screen["ocr"]}
    markers = {m["id"]: m for m in screen.get("markers", [])}
    result = []
    matched_ids = set()
    for item in relevant:
        descriptor = item["descriptor"]
        matches = []
        for control in controls:
            if (
                not control.enabled
                or (control.confidence_policy == "threshold" and control.confidence < threshold)
                or control.role != descriptor["role"]
            ):
                continue
            labels = [blocks[i] for i in control.label_ids if i in blocks]
            name = " ".join(
                b["text"] for b in sorted(labels, key=lambda b: (b["bbox"][1], b["bbox"][0]))
            )
            region = item.get("input_region")
            if item.get("input_image_size") and screen.get("image_size") not in (
                None,
                item["input_image_size"],
            ):
                region = None  # A resized window invalidates the transient extent.
            marker = markers.get(control.id)
            if (
                region is not None and marker is not None and overlap(region, marker["bbox"]) >= 0.7
            ) or (region is None and name == (descriptor.get("label") or descriptor["name"])):
                matches.append(control)
        matched_ids.update(c.id for c in matches)
        if not matches:
            result.append(item)
    return result, matched_ids


def sanitize_unused_controls(
    services: MatchServices,
    phase: str,
    response: ScreenMatch,
    screen: dict,
    required: list[dict] | tuple[dict, ...],
):
    """Discard unrelated role mistakes and impossible parent links before grounding."""
    markers = {m["id"]: m for m in screen.get("markers", [])}
    controls = {c.id: c for c in response.controls}
    ignored_save_result = phase != "save_result" and response.save_result is not None
    if ignored_save_result:
        response = response.model_copy(update={"save_result": None, "message_targets": []})
    direct_needed = (
        {s.target for s in response.steps}
        | {e.target for e in response.evidence}
        | {e.target for e in response.completion_evidence}
    )
    if response.save_result is not None:
        direct_needed.update(response.message_targets)
    direct_needed.update(i for i in (response.grid, response.row) if i is not None)
    # Verification controls are mandatory even when they are not action targets.
    if required:
        direct_needed.update(
            verification_requirements(
                response.controls,
                screen,
                required,
                response.screen_kind,
                services.settings.visual_min_confidence,
            )[1]
        )
    needed = set(direct_needed)
    for control_id in list(needed):
        visited = set()
        while control_id in controls and control_id not in visited:
            visited.add(control_id)
            control = controls[control_id]
            parent_id = control.parent_id
            if parent_id is None or parent_id not in controls:
                break
            child_marker = markers.get(control_id)
            parent_marker = markers.get(parent_id)
            parent = controls[parent_id]
            if (
                child_marker is None
                or parent_marker is None
                or not marker_role_allowed(
                    parent_marker["source"],
                    parent.role,
                    allow_ocr_inputs=services.settings.visual_model_led_semantics,
                )
                or not contains(tuple(parent_marker["bbox"]), tuple(child_marker["bbox"]))
            ):
                break
            needed.add(parent_id)
            control_id = parent_id
    invalid = {
        c.id
        for c in response.controls
        if c.id in markers
        and not marker_role_allowed(
            markers[c.id]["source"],
            c.role,
            allow_ocr_inputs=services.settings.visual_model_led_semantics,
        )
    }
    # Children depend on a parent's classification; never leave dangling parents.
    removed = invalid - direct_needed
    while True:
        descendants = {c.id for c in response.controls if c.parent_id in removed}
        expanded = removed | (descendants - needed)
        if expanded == removed:
            break
        removed = expanded
    controls = [c for c in response.controls if c.id not in removed]
    retained_control_ids = {c.id for c in controls}
    invalid_parents = []
    normalized = []
    for control in controls:
        child = markers.get(control.id)
        parent = markers.get(control.parent_id) if control.parent_id is not None else None
        parent_removed = control.parent_id in removed
        if control.parent_id is not None and (
            parent_removed
            or control.parent_id not in retained_control_ids
            or parent is None
            or child is None
            or not contains(tuple(parent["bbox"]), tuple(child["bbox"]))
        ):
            invalid_parents.append({"marker_id": control.id, "parent_id": control.parent_id})
            control = control.model_copy(update={"parent_id": None})
        normalized.append(control)
    if removed:
        services.log.append(
            "verify",
            payload={"check": "unused_invalid_roles_omitted", "marker_ids": sorted(removed)},
        )
    if ignored_save_result:
        services.log.append(
            "verify",
            payload={"check": "out_of_phase_save_result_omitted"},
        )
    if invalid_parents:
        services.log.append(
            "verify",
            payload={
                "check": "unused_invalid_parent_links_omitted",
                "links": invalid_parents,
            },
        )
    return response.model_copy(update={"controls": normalized})


def required_verification(pending: PendingAction | None) -> list[dict]:
    """Carry identities and transient input regions, never previously read values."""
    required = []
    if pending is not None:
        execution = pending.get("execution")
        delivered = execution.delivered if execution else [pending]
        for item in delivered:
            decision = item["decision"]
            descriptor = item.get("descriptor")
            if descriptor and decision.intent in {"query_input", "fill"}:
                entry = {
                    "source": item["choice"].source,
                    "screen": decision.screen_kind,
                    "descriptor": descriptor,
                }
                before = item.get("before_observation")
                if before is not None:
                    target = next((e for e in before.elements if e.ref == item["choice"].ref), None)
                    if target is not None:
                        entry["input_region"] = list(target.bbox)
                        entry["input_image_size"] = [
                            before.screenshot.width,
                            before.screenshot.height,
                        ]
                if entry not in required:
                    required.append(entry)
    return required


def retain_verification(
    services: MatchServices,
    response: ScreenMatch,
    screen: dict,
    required: list[dict] | tuple[dict, ...],
    page=None,
):
    if services.settings.visual_model_only_review:
        services.log.append(
            "verify",
            payload={
                "check": "required_verification_delegated",
                "reviewer": "screen_model",
                "sources": sorted(item["source"] for item in required),
            },
        )
        return response

    def missing(controls):
        absent, _ = verification_requirements(
            controls,
            screen,
            list(required),
            response.screen_kind,
            services.settings.visual_min_confidence,
        )
        # In model-led mode the worker can bind a field directly to CURRENT
        # visual evidence.  That binding is stronger than trying to recover the
        # old, empty input extent by IoU after the value has changed the OCR box.
        # The graph still rereads the bound element and compares its literal OCR
        # value before any proposed next action is eligible.
        if services.settings.visual_model_led_semantics:
            by_id = {control.id: control for control in controls}
            explicit = {
                evidence.field
                for evidence in response.evidence
                if (
                    (control := by_id.get(evidence.target)) is not None
                    and control.enabled
                    and control.role in {"edit", "combobox"}
                    and (
                        control.confidence_policy != "threshold"
                        or control.confidence >= services.settings.visual_min_confidence
                    )
                )
            }
            absent = [item for item in absent if item["source"] not in explicit]
        return absent

    absent = missing(response.controls)
    if not absent:
        return response
    # Only an actual omission causes this measured repair call. The action/proof
    # proposal remains unchanged; original verification still checks current values.
    supplement = _call_match(
        services,
        "target_grounding",
        "Return only missing verification controls and their necessary parents from CURRENT "
        "OCR/markers. Preserve their labels and literal contained text. No actions or invented "
        "values/boxes. Report uncertainty if they cannot be observed.\n"
        "All references, including label_ids/text_ids, use the printed text marker IDs.\n"
        + json.dumps(
            {"required_verification": absent, "screen": model_screen(screen)}, ensure_ascii=False
        ),
        SemanticDetection,
        page=page,
    )
    supplement = local_response(supplement, screen)
    controls = {c.id: c for c in response.controls}
    controls.update({c.id: c for c in supplement.controls})
    response = response.model_copy(
        update={
            "controls": list(controls.values()),
            "uncertainties": [*response.uncertainties, *supplement.uncertainties],
        }
    )
    remaining = missing(response.controls)
    services.log.append(
        "verify",
        payload={
            "check": "required_verification_repaired",
            "sources": [r["source"] for r in absent],
            "ok": not remaining,
        },
    )
    if remaining:
        raise GuardStop("required_verification_missing")
    return response


def retain_action_targets(
    services: MatchServices, response: ScreenMatch, screen: dict, page=None
) -> ScreenMatch:
    """Add semantics for model-selected action and evidence targets."""
    controls = {control.id: control for control in response.controls}
    required = (
        {step.target for step in response.steps}
        | {evidence.target for evidence in response.evidence}
        | {evidence.target for evidence in response.completion_evidence}
    )
    missing = required - controls.keys()
    if not missing:
        return response

    marker_ids = {marker["id"] for marker in screen.get("markers", [])}
    # Let the normal semantic-grounding retry repair an invalid interpretation.
    # This helper handles only a sound interpretation that omitted a real target.
    if not controls.keys() <= marker_ids:
        return response
    if not missing <= marker_ids:
        return response

    original_steps = response.steps
    supplement = _call_match(
        services,
        "input_evidence_recovery",
        TARGET_GROUNDING_RULES
        + json.dumps(
            {
                "required_target_ids": sorted(missing),
                "existing_controls": model_controls(response.controls, screen),
                "screen": model_screen(screen),
            },
            ensure_ascii=False,
        ),
        SemanticDetection,
        page=page,
    )
    supplement = local_response(supplement, screen)
    supplied = {control.id: control for control in supplement.controls}
    if not missing <= supplied.keys():
        raise GuardStop("action_target_semantics_missing")

    # Preserve every prior meaning. Only the omitted targets and their explicit
    # parent chain may be added by this repair call.
    allowed = set(missing)
    for target_id in tuple(missing):
        current = supplied.get(target_id)
        visited: set[int] = set()
        while current is not None and current.parent_id is not None:
            if current.parent_id in visited:
                raise GuardStop("action_target_parent_cycle")
            visited.add(current.parent_id)
            allowed.add(current.parent_id)
            current = supplied.get(current.parent_id)
    controls.update({key: value for key, value in supplied.items() if key in allowed})
    repaired = response.model_copy(
        update={
            "controls": list(controls.values()),
            "uncertainties": [*response.uncertainties, *supplement.uncertainties],
        }
    )
    if repaired.steps != original_steps:
        raise AssertionError("action_target_grounding_changed_plan")
    write_match_artifact(services, "target-grounding", repaired)
    services.log.append(
        "verify",
        payload={
            "check": "action_targets_grounded",
            "marker_ids": sorted(missing),
            "procedure_step": response.procedure_step,
            "plan_preserved": True,
        },
    )
    return repaired


def bind_value_evidence_to_controls(
    services: MatchServices, response: ScreenMatch, screen: dict
) -> ScreenMatch:
    """Resolve a literal OCR value marker to its already-grounded input control."""

    markers = {marker["id"]: marker for marker in screen.get("markers", [])}
    controls = {control.id: control for control in response.controls}
    value_roles = {"edit", "combobox", "cell", "dataitem", "row"}
    rebound = []
    changes = []
    for evidence in response.evidence:
        marker = markers.get(evidence.target)
        if marker is None or marker.get("source") != "ocr":
            rebound.append(evidence)
            continue
        value_ids = set(marker.get("text_ids", []))
        candidates = [
            control.id
            for control in controls.values()
            if control.role in value_roles and value_ids and value_ids <= set(control.text_ids)
        ]
        if len(candidates) != 1:
            rebound.append(evidence)
            continue
        rebound.append(evidence.model_copy(update={"target": candidates[0]}))
        changes.append(
            {"field": evidence.field, "value_marker": evidence.target, "control": candidates[0]}
        )
    if not changes:
        return response
    services.log.append(
        "verify",
        payload={"check": "value_evidence_bound_to_control", "bindings": changes},
    )
    return response.model_copy(update={"evidence": rebound})


def bind_selection_effect(
    response: ScreenMatch, step: ActionCapability, active_fields: set[str]
) -> ScreenMatch:
    """Bind a unique capability-owned input effect to the final selection click."""
    selection_clicks = all(
        item.intent == "select_option" and item.action == "click" for item in response.steps
    )
    if (
        response.operation != "act"
        or not response.steps
        or not selection_clicks
        or response.steps[-1].source is not None
    ):
        return response
    candidates = [field for field in step.input_scope if field in active_fields]
    if len(candidates) != 1:
        return response
    steps = [*response.steps]
    steps[-1] = steps[-1].model_copy(update={"source": candidates[0]})
    return response.model_copy(update={"steps": steps})


_REPLANNABLE_MATCH_ERRORS = {
    "screen_match_action_outside_procedure",
    "screen_match_field_outside_procedure_scope",
    "screen_match_login_batch_incomplete",
    "screen_match_completion_decision_required",
    "screen_match_model_point_required",
    "screen_match_mixed_boundary_actions",
    "screen_match_step_not_executable",
    "screen_match_task_surface_mismatch",
    "screen_match_taught_step_required",
    "screen_match_saved_row_target_mismatch",
    "screen_match_action_target_uncertain",
    "screen_match_action_surface_mismatch",
}

# Observe-only disables policy rejection, but these responses cannot be
# represented by the common executor at all. Let the bounded plan correction
# repair their structure instead of failing later in compile_match and paying
# for a fresh full-screen decision on the same pixels.
_EXECUTOR_CONTRACT_ERRORS = {
    "screen_match_login_batch_incomplete",
    "screen_match_completion_decision_required",
    "screen_match_model_point_required",
    "screen_match_mixed_boundary_actions",
    "screen_match_step_not_executable",
}


def _is_login_batch(steps: list[MatchAction]) -> bool:
    """Return whether steps form the single login boundary accepted by the executor."""

    return (
        len(steps) >= 2
        and steps[-1].intent == "login"
        and steps[-1].action == "click"
        and all(
            action.intent == "credential"
            and action.action == "fill"
            and (action.source or "").startswith("credential.")
            for action in steps[:-1]
        )
    )


def _validate_taught_step(response: ScreenMatch, request: MatchRequest) -> None:
    taught = request.task_instruction.procedure
    if not taught:
        return
    taught_ids = [item.id for item in taught]
    if response.operation in {"act", "complete"} and response.taught_step_id is None:
        raise ValueError("screen_match_taught_step_required")
    if response.taught_step_id is not None and response.taught_step_id not in taught_ids:
        raise ValueError("screen_match_taught_step_required")


def _semantic_label(text: str) -> str:
    """Normalize user-taught and OCR labels without application-specific aliases."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^0-9a-z가-힣]+", "", normalized)


def _validate_saved_row_navigation_target(response: ScreenMatch, request: MatchRequest) -> None:
    """A post-save query may open only a target carrying the saved identity value."""

    identity_key = request.task_instruction.identity_input_key
    if (
        request.saved_record_review is None
        or identity_key is None
        or response.screen_kind != "query"
    ):
        return
    navigations = [
        step
        for step in response.steps
        if step.intent == "navigate" and step.action in {"click", "double_click"}
    ]
    if not navigations:
        return
    expected = _semantic_label(request.values[identity_key])
    ocr = {item["id"]: _semantic_label(item["text"]) for item in request.screen.get("ocr", [])}
    markers = {marker["id"]: marker for marker in request.screen.get("markers", [])}
    controls = {control.id: control for control in response.controls}
    for step in navigations:
        control = controls.get(step.target)
        text_ids = set(control.text_ids) if control is not None else set()
        marker = markers.get(step.target)
        if marker is not None and marker.get("source") == "ocr":
            text_ids.update(marker.get("text_ids", []))
        if expected and expected in {ocr.get(text_id, "") for text_id in text_ids}:
            continue
        raise ValueError("screen_match_saved_row_target_mismatch")


def _normalize_match_actions(
    response: ScreenMatch, request: MatchRequest, services: MatchServices
) -> ScreenMatch:
    """Normalize provider vocabulary at the model boundary."""

    normalized = []
    for item in response.steps:
        source = normalize_action_source(
            intent=item.intent,
            candidate=item.source,
            inputs=request.values,
        )
        item = item.model_copy(update={"source": source})
        if (
            request.phase == "task"
            and item.action == "fill"
            and item.source is not None
            and not item.source.startswith("credential.")
        ):
            item = item.model_copy(update={"intent": "fill"})
        elif item.action == "key" and item.intent == "fill" and item.source is None:
            item = item.model_copy(update={"intent": "navigate"})
            services.log.append(
                "verify",
                payload={
                    "check": "nonwriting_fill_intent_normalized",
                    "action": "key",
                    "keys": item.keys,
                    "target": item.target,
                    "intent": "navigate",
                },
            )
        normalized.append(item)
    evidence = [
        item.model_copy(
            update={"field": resolve_input_key(item.field, request.values) or item.field}
        )
        for item in response.evidence
    ]
    return response.model_copy(update={"steps": normalized, "evidence": evidence})


def _validate_completion_annotations(
    response: ScreenMatch, request: MatchRequest, services: MatchServices
) -> ScreenMatch:
    if any(item.field not in request.values for item in response.evidence):
        raise ValueError("screen_match_unknown_task_field")
    if response.operation == "complete" and not response.completion_evidence:
        raise ValueError("screen_match_task_completion_evidence_required")
    if response.operation != "complete" and response.completion_evidence:
        services.log.append(
            "verify",
            payload={
                "check": "premature_completion_evidence_omitted",
                "count": len(response.completion_evidence),
            },
        )
        response = response.model_copy(update={"completion_evidence": []})
    if any(
        item.condition_index >= len(request.task_instruction.completion_conditions)
        for item in response.completion_evidence
    ):
        raise ValueError("screen_match_task_completion_condition_unknown")
    return response


def _validate_login_boundary(response: ScreenMatch, request: MatchRequest) -> None:
    if any(action.intent == "credential" for action in response.steps) and not _is_login_batch(
        response.steps
    ):
        raise ValueError("screen_match_login_batch_incomplete")
    if not _is_login_batch(response.steps):
        return
    expected = set(request.policy.facts.remaining_credentials)
    planned = {action.source for action in response.steps[:-1] if action.source is not None}
    if expected and planned != expected:
        raise ValueError("screen_match_login_batch_incomplete")


def _validate_step_contract(
    response: ScreenMatch, request: MatchRequest, services: MatchServices
) -> None:
    """Reject combinations that cannot be represented by Decision/Choice."""

    if response.operation == "stop" and request.policy.facts.final_evidence_ready:
        raise ValueError("screen_match_completion_decision_required")
    marker_ids = {marker["id"] for marker in request.screen.get("markers", [])}
    controls = {control.id: control for control in response.controls}
    for step in response.steps:
        if step.action in {"click", "double_click"} and step.point is None:
            raise ValueError("screen_match_step_not_executable")
        writes_value = step.intent in {"credential", "fill"} or step.action == "fill"
        if writes_value and not (
            step.action == "fill"
            and step.intent in {"credential", "fill"}
            and step.source is not None
        ):
            raise ValueError("screen_match_step_not_executable")
        control = controls.get(step.target)
        model_created = (
            services.settings.guard_mode == "observe_only"
            and step.target not in marker_ids
            and control is not None
            and control.confidence_policy == "argmax"
        )
        if model_created and step.point is None:
            raise ValueError("screen_match_model_point_required")


def _valid_multi_action_boundary(steps: list[MatchAction]) -> bool:
    intents = {action.intent for action in steps}
    if _is_login_batch(steps) or intents == {"fill"}:
        return True
    if intents == {"select_option"} and all(action.action == "click" for action in steps):
        return True
    selection_then_navigation = steps[-1].intent == "navigate" and all(
        action.intent == "select_option" for action in steps[:-1]
    )
    if selection_then_navigation and all(
        action.action in {"click", "double_click"} and action.source is None for action in steps
    ):
        return True
    return intents in ({"navigate"}, {"select_tab"}) and all(
        action.source is None for action in steps
    )


def _validate_action_boundary(
    response: ScreenMatch,
    capability: ActionCapability,
    request: MatchRequest,
) -> None:
    allowed_intents = set(capability.actions)
    if "login" in allowed_intents:
        allowed_intents.add("credential")
    if request.phase != "save_result" and any(
        action.intent not in allowed_intents for action in response.steps
    ):
        raise ValueError("screen_match_action_outside_procedure")
    if len(response.steps) > 1 and not _valid_multi_action_boundary(response.steps):
        raise ValueError("screen_match_mixed_boundary_actions")


def _validate_input_scope(response: ScreenMatch, capability: ActionCapability) -> None:
    scoped_sources = {
        action.source
        for action in response.steps
        if action.source is not None and not action.source.startswith("credential.")
    }
    if capability.input_scope and not scoped_sources <= set(capability.input_scope):
        raise ValueError("screen_match_field_outside_procedure_scope")


def _validate_action_targets_executable(response: ScreenMatch, services: MatchServices) -> None:
    """Reject a target that the subsequent visual binding will disable."""

    controls = {control.id: control for control in response.controls}
    for step in response.steps:
        control = controls.get(step.target)
        if control is None:
            continue
        uncertain = (
            control.confidence_policy == "threshold"
            and control.confidence < services.settings.visual_min_confidence
        )
        if not control.enabled or uncertain:
            raise ValueError("screen_match_action_target_uncertain")


def validate_match_plan(
    response: ScreenMatch,
    capability: ActionCapability,
    request: MatchRequest,
    services: MatchServices,
    active_fields: set[str],
) -> ScreenMatch:
    """Validate task actions separately from an already grounded screen interpretation."""
    response = _normalize_match_actions(response, request, services)
    _validate_step_contract(response, request, services)
    if services.settings.guard_mode == "enforce":
        _validate_taught_step(response, request)
        response = _validate_completion_annotations(response, request, services)
        _validate_saved_row_navigation_target(response, request)
        _validate_login_boundary(response, request)
        _validate_action_boundary(response, capability, request)
    else:
        checks = (
            ("taught_step", lambda: _validate_taught_step(response, request)),
            (
                "completion_annotations",
                lambda: _validate_completion_annotations(response, request, services),
            ),
            (
                "saved_row_navigation_target",
                lambda: _validate_saved_row_navigation_target(response, request),
            ),
            ("login_boundary", lambda: _validate_login_boundary(response, request)),
            ("action_boundary", lambda: _validate_action_boundary(response, capability, request)),
        )
        for check, validate in checks:
            try:
                validate()
            except ValueError as error:
                services.log.append(
                    "verify",
                    payload={
                        "check": "guard_observe_only",
                        "guard": check,
                        "would_reject": str(error),
                    },
                )
                if str(error) in _EXECUTOR_CONTRACT_ERRORS:
                    raise
    response = bind_selection_effect(response, capability, active_fields)
    if services.settings.guard_mode == "enforce":
        _validate_input_scope(response, capability)
        _validate_action_targets_executable(response, services)
    else:
        for check, validate in (
            ("input_scope", lambda: _validate_input_scope(response, capability)),
            ("action_target", lambda: _validate_action_targets_executable(response, services)),
        ):
            try:
                validate()
            except ValueError as error:
                services.log.append(
                    "verify",
                    payload={
                        "check": "guard_observe_only",
                        "guard": check,
                        "would_reject": str(error),
                    },
                )
    return response


def _action_targets_have_groundable_roles(response: ScreenMatch, screen: dict) -> bool:
    """Return whether every immediate target can carry its proposed semantic role."""

    markers = {marker["id"]: marker for marker in screen.get("markers", [])}
    controls = {control.id: control for control in response.controls}
    for step in response.steps:
        marker = markers.get(step.target)
        control = controls.get(step.target)
        if marker is None or control is None:
            return False
        if not marker_role_allowed(marker.get("source", ""), control.role):
            return False
    return True


def omit_nonblocking_refinement(
    services: MatchServices, response: ScreenMatch, screen: dict
) -> ScreenMatch:
    """Do not refine unrelated future work when the current action is grounded."""
    if not response.needs_refinement or response.operation != "act" or not response.steps:
        return response
    control_ids = {control.id for control in response.controls}
    action_targets = {step.target for step in response.steps}
    if not action_targets <= control_ids or not _action_targets_have_groundable_roles(
        response, screen
    ):
        return response
    services.log.append(
        "verify",
        payload={
            "check": "nonblocking_refinement_omitted",
            "action_targets": sorted(action_targets),
            "reason": "current_action_controls_already_defined",
        },
    )
    return response.model_copy(update={"needs_refinement": False})


def omit_nonblocking_image_request(
    services: MatchServices, response: ScreenMatch, screen: dict
) -> ScreenMatch:
    """A grounded immediate action does not need another copy of the same image."""
    if not response.needs_image or response.operation != "act" or not response.steps:
        return response
    control_ids = {control.id for control in response.controls}
    action_targets = {step.target for step in response.steps}
    if not action_targets <= control_ids or not _action_targets_have_groundable_roles(
        response, screen
    ):
        return response
    services.log.append(
        "verify",
        payload={
            "check": "nonblocking_image_request_omitted",
            "action_targets": sorted(action_targets),
            "reason": "current_action_controls_already_defined",
        },
    )
    return response.model_copy(update={"needs_image": False})


_ACTION_ROLES = {
    "fill": {"edit", "combobox", "cell"},
    "click": {
        "edit",
        "combobox",
        "button",
        "checkbox",
        "tabitem",
        "menuitem",
        "row",
        "cell",
        "icon",
    },
    "double_click": {"button", "menuitem", "row", "cell", "icon"},
    "key": {
        "edit",
        "combobox",
        "button",
        "checkbox",
        "tabitem",
        "menuitem",
        "table",
        "row",
        "cell",
        "group",
        "window",
    },
}


def validate_action_surfaces(
    services: MatchServices, response: ScreenMatch, screen: dict
) -> ScreenMatch:
    """Keep text as evidence and permit actions only on typed current surfaces."""

    markers = {marker["id"]: marker for marker in screen.get("markers", [])}
    controls = {control.id: control for control in response.controls}
    rejected = []
    for step in response.steps:
        marker = markers.get(step.target)
        control = controls.get(step.target)
        source = marker.get("source", "") if marker is not None else ""
        reason = None
        if marker is None:
            # Preserve the existing unknown-marker error path.  This validator
            # distinguishes known text evidence from known action geometry.
            continue
        elif step.action not in allowed_marker_actions(source):
            reason = "action_not_allowed_for_geometry"
        elif (
            control is not None
            and not (source == "ocr" and step.action in {"click", "double_click"})
            and control.role not in _ACTION_ROLES[step.action]
        ):
            reason = "role_not_actionable"
        if reason is not None:
            rejected.append(
                {
                    "target": step.target,
                    "action": step.action,
                    "source": source or None,
                    "role": control.role if control is not None else None,
                    "reason": reason,
                }
            )
    if not rejected:
        return response
    services.log.append(
        "verify",
        payload={"check": "action_surface_rejected", "targets": rejected},
    )
    raise ValueError("screen_match_action_surface_mismatch")


def enforce_taught_input_surfaces(
    request: MatchRequest, services: MatchServices, response: ScreenMatch
) -> ScreenMatch:
    """Keep a taught record input off a grid unless the user authorized that surface."""

    task = request.task_instruction
    if not task.procedure or not response.steps:
        return response
    controls = {control.id: control for control in response.controls}
    invalid = []
    for step in response.steps:
        if step.action != "fill" or step.source is None:
            continue
        source = resolve_input_key(step.source, request.values) or step.source
        control = controls.get(step.target)
        if control is None:
            # Missing action roles are grounded by retain_action_targets before execution.
            continue
        parents = []
        parent_id = control.parent_id
        seen = {control.id}
        while parent_id in controls and parent_id not in seen:
            seen.add(parent_id)
            parent = controls[parent_id]
            parents.append(parent.role)
            parent_id = parent.parent_id
        actual = input_surface(control.role, parents)
        surfaces = allowed_input_surfaces(task, source, request.values)
        if actual is not None and actual not in surfaces:
            invalid.append((step, source, actual, sorted(surfaces)))
    if not invalid:
        return response
    invalid_steps = {id(item[0]) for item in invalid}
    retained = [step for step in response.steps if id(step) not in invalid_steps]
    services.log.append(
        "verify",
        payload={
            "check": "taught_input_surface_mismatch_deferred",
            "rejected": [
                {
                    "source": source,
                    "target": step.target,
                    "observed_surface": actual,
                    "allowed_surfaces": surfaces,
                }
                for step, source, actual, surfaces in invalid
            ],
            "safe_steps_retained": len(retained),
        },
    )
    if response.operation == "act" and not retained:
        raise ValueError("screen_match_task_surface_mismatch")
    invalid_targets = {item[0].target for item in invalid}
    invalid_sources = {item[1] for item in invalid}
    evidence = [
        item
        for item in response.evidence
        if item.target not in invalid_targets and item.field not in invalid_sources
    ]
    return response.model_copy(update={"steps": retained, "evidence": evidence})


def task_action_contract(
    phase: str,
    instruction: TaskInstruction,
    contract: dict,
    active_fields: set[str],
) -> ActionCapability:
    """Expose runtime capabilities without encoding an application route."""
    actions = set(contract["allowed_intents"])
    actions.difference_update({"stop", "verify"})
    if "credential" in actions:
        actions.remove("credential")
        actions.add("login")
    if phase == "task":
        actions.add("complete")
    return ActionCapability(
        input_scope=sorted(active_fields),
        actions=sorted(actions),
        goal=instruction.objective[:500],
        completion="; ".join(instruction.completion_conditions)[:500],
    )


def prepare_match_plan(
    request: MatchRequest,
    services: MatchServices,
    response: ScreenMatch,
    capability: ActionCapability,
    active_fields: set[str],
):
    """Compile every proposal through the same task and write-surface contract."""
    response = response.model_copy(
        update={
            "purpose": (response.purpose or response.procedure_step)[:160],
            "procedure_step": capability.capability_id,
        }
    )
    if request.phase == "task" and response.operation == "duplicate_clear" and response.steps:
        # A combined task response may report what the current query proves and
        # also choose the next visible action.  The proof operation cannot carry
        # actions in the executor contract, so retain the model's executable
        # choice as an ordinary task action instead of discarding it and paying
        # for an identical follow-up decision.
        services.log.append(
            "verify",
            payload={
                "check": "compound_response_normalized",
                "guard": "proof_with_actions",
                "reported_operation": response.operation,
                "outcome": "normalized_to_action",
                "retained_actions": len(response.steps),
            },
        )
        response = response.model_copy(update={"operation": "act", "grid": None, "row": None})
    elif response.operation != "act" and response.steps:
        services.log.append(
            "verify",
            payload={
                "check": "compound_response_normalized",
                "guard": "proof_with_actions",
                "accepted_operation": response.operation,
                "ignored_actions": len(response.steps),
            },
        )
        response = response.model_copy(update={"steps": []})
    response = validate_action_surfaces(services, response, request.screen)
    response = enforce_taught_input_surfaces(request, services, response)
    return validate_match_plan(response, capability, request, services, active_fields)


def build_match_prompt(request: MatchRequest, services: MatchServices) -> MatchPromptContext:
    """Assemble the bounded model context without selecting or mutating a plan."""

    contract = request.execution_contract
    required = request.required_verification
    pending_verification_fields = sorted(
        {
            item["source"]
            for item in contract["pending_actions"]
            if item["intent"] == "fill" and item["source"] in request.values
        }
    )
    # Generic input keys are reusable across screens. Keep action eligibility
    # broad while exposing a separate, model-owned coverage ledger. Calling
    # every source "remaining" forever made matching values look unfinished
    # and obscured the few fields that had never been observed.
    available_inputs = sorted(request.values)
    remaining_inputs = sorted(set(request.values) - request.verified_fields)
    contract = {
        **contract,
        "available_inputs": available_inputs,
        "remaining_inputs": remaining_inputs,
    }
    # Pending fields remain in the prompt so the current pixels can verify
    # them, but the conditional next action assumes that verification passes.
    # Re-adding them to action eligibility lets a fallback plan repeat work
    # that the executor has already delivered.
    active_fields = set(available_inputs)
    verified_fields = set(request.verified_fields)
    capability = task_action_contract(
        request.phase, request.task_instruction, contract, active_fields
    )
    record = dict(request.values)
    context_layers = request.context_layers

    notebook = {
        "phase": request.phase,
        "checkpoint": request.policy.current_step,
        "pending_verification": request.pending_verification,
        "pending_actions": contract["pending_actions"],
        # 누적 raw feedback 대신 정제된 스크래치패드와 최근 N턴 버퍼를 주입한다.
        "scratchpad": context_layers["scratchpad"],
        "recent_turns": context_layers["recent_turns_buffer"],
        "recent_feedback": list(request.recent_feedback),
        "required_verification": required,
        "available_credentials": list(request.available_credentials),
    }
    if request.phase == "task":
        notebook.update(
            {
                "verified_inputs": sorted(request.verified_fields),
                "evidence_claims": list(request.evidence_claims),
                "failed_transitions": list(request.failed_transitions),
                "anticipated_verified_inputs": (
                    sorted(verified_fields) if contract["conditional"] else []
                ),
                "remaining_inputs": remaining_inputs,
                "available_inputs": available_inputs,
                "pending_verification_inputs": pending_verification_fields,
                MODEL_INPUTS_NAME: record,
                "workflow_state": dict(request.workflow_flags),
            }
        )
        if request.saved_record_review is not None:
            # This is workflow state, not a second value judge.  It tells the
            # planner whether a post-save query has actually produced a result;
            # otherwise an editor left open after Save can be mistaken for the
            # required re-queried record.
            notebook["saved_record_review"] = request.saved_record_review
    else:
        notebook.update(
            {
                "verified_fields": sorted(request.verified_fields),
                "anticipated_verified_fields": (
                    sorted(verified_fields) if contract["conditional"] else []
                ),
                "remaining_fields": remaining_inputs,
                "pending_verification_fields": pending_verification_fields,
                **request.workflow_flags,
                "record": record,
            }
        )
    state = {
        "notebook": notebook,
        "screen": model_screen(request.screen),
        "execution": contract,
    }
    if request.post_action_review:
        state["post_action_review"] = {
            "mode": "field_evidence_and_next_action",
            "delivered_sources": pending_verification_fields,
        }
    # User education is the semantic source of truth for field-to-input bindings.
    # Omitting it left the model with input keys and pixels but no company-specific
    # explanation of what each visible field means.
    # The focused procedure window below already carries the applicable user
    # teaching. Repeating every distant step here wastes context and lets stale
    # routes compete with the current screen.
    state["task"] = {
        **request.task_instruction.model_dump(mode="json", exclude={"procedure"}),
        "procedure": [],
    }
    # The digest is the user-education contract boundary.  Two companies can
    # show identical pixels, and one company's memo can later be revised
    # without changing its display name.  Neither case may reuse a decision
    # compiled under another education file.
    state["task_contract_sha256"] = request.task_instruction_sha256
    state["taught_procedure_progress"] = request.taught_procedure_progress
    state["capability"] = capability.model_dump()
    if contract["conditional"]:
        state["proposal_after_verification"] = {
            "condition": contract["condition"],
            "checkpoint": contract["checkpoint"],
        }
    if "save_review" in contract:
        state["save_review"] = contract["save_review"]
    methods = services.methods
    if methods.enabled:
        state["method_memory"] = methods.prompt_context()
    method_rules = METHOD_RULES if methods.enabled else ""
    guard_rules = (
        OBSERVE_ONLY_TARGET_RULES if services.settings.guard_mode == "observe_only" else ""
    )
    taught_rules = TAUGHT_PROCEDURE_RULES if request.task_instruction.procedure else ""
    prompt = (
        TASK_RULES
        + guard_rules
        + taught_rules
        + method_rules
        + (POST_ACTION_REVIEW_RULES if request.post_action_review else "")
        + json.dumps(state, ensure_ascii=False)
    )
    return MatchPromptContext(
        state=state,
        capability=capability,
        active_fields=frozenset(active_fields),
        prompt=prompt,
        guard_rules=guard_rules,
        taught_rules=taught_rules,
        method_rules=method_rules,
    )


def select_match_response(
    request: MatchRequest,
    services: MatchServices,
    prepared: MatchPromptContext,
    *,
    allow_method: bool = True,
) -> MatchSelection:
    """Resolve method, cache, grounded-plan and model sources in priority order."""

    screen, page = request.screen, request.page
    method_proposal = request.proposal if allow_method else None
    if (
        allow_method
        and method_proposal is None
        and request.phase != "save_result"
        and "correction" not in screen
    ):
        method_proposal = services.methods.apply(screen)
    cache_hit = services.cache.lookup(prepared.state) if method_proposal is None else None
    if method_proposal is not None:
        response = method_proposal
    elif cache_hit is not None:
        _, cached = cache_hit
        response = ScreenMatch.model_validate(cached)
        services.log.append(
            "cache_hit",
            payload={
                "check": "visual_match_cache",
                "key": cache_hit[0],
                "procedure_step": response.procedure_step,
            },
        )
    elif request.grounded_interpretation is not None:
        grounded = request.grounded_interpretation
        plan_state = {
            **prepared.state,
            "grounded_interpretation": {
                "screen_kind": grounded.screen_kind,
                "controls": model_controls(grounded.controls, screen),
                "uncertainties": grounded.uncertainties,
            },
        }
        plan = _call_match(
            services,
            "grounded_screen_plan",
            TASK_PLAN_RULES
            + prepared.guard_rules
            + prepared.taught_rules
            + prepared.method_rules
            + json.dumps(plan_state, ensure_ascii=False),
            ScreenPlan,
            page=page,
        )
        response = ScreenMatch.model_validate({**grounded.model_dump(), **plan.model_dump()})
        write_match_artifact(services, "cached-plan", response)
        services.log.append(
            "verify",
            payload={
                "check": "grounded_interpretation_planned",
                "controls": len(grounded.controls),
            },
        )
    else:
        response = _call_match(
            services,
            (
                "input_review"
                if request.post_action_review
                else "completion_review"
                if request.workflow_flags.get("save_issued")
                else "screen_decision"
            ),
            prepared.prompt,
            ScreenMatch,
            page=page,
        )
        write_match_artifact(services, "markers", response)
        response = local_response(response, screen)
    if response.requested_instruction_ids:
        by_id = {step.id: step for step in request.task_instruction.procedure}
        requested = list(dict.fromkeys(response.requested_instruction_ids))
        if any(step_id not in by_id for step_id in requested):
            raise ValueError("unknown_requested_instruction")
        expanded = {
            **prepared.state,
            "taught_procedure_progress": {
                **request.taught_procedure_progress,
                "steps": [by_id[step_id].model_dump(mode="json") for step_id in requested],
                "retrieved_step_ids": requested,
            },
        }
        services.log.append(
            "verify",
            payload={
                "check": "education_details_retrieved",
                "step_ids": requested,
                "frame": str(page.image_path) if page else None,
                "discarded_provisional_actions": len(response.steps),
                "education_utf8_bytes": len(
                    json.dumps(expanded["taught_procedure_progress"], ensure_ascii=False).encode(
                        "utf-8"
                    )
                ),
            },
        )
        prepared = replace(prepared, state=expanded)
        response = _call_match(
            services,
            "education_retrieval",
            TASK_RULES
            + prepared.guard_rules
            + prepared.taught_rules
            + prepared.method_rules
            + json.dumps(expanded, ensure_ascii=False)
            + "\nRequested details are present. Return the decision, not another retrieval.",
            ScreenMatch,
            page=page,
        )
        write_match_artifact(services, "education-plan", response)
        response = local_response(response, screen)
        if response.requested_instruction_ids:
            raise ValueError("education_retrieval_unresolved")
        cache_hit = None
        method_proposal = None
    response = omit_nonblocking_image_request(services, response, screen)
    used_image = cache_hit is None and method_proposal is None
    if response.needs_image:
        response = _call_match(
            services,
            "image_recovery",
            TASK_RULES
            + prepared.guard_rules
            + prepared.taught_rules
            + prepared.method_rules
            + json.dumps(prepared.state, ensure_ascii=False)
            + "\nResolve the reported ambiguity with this image.",
            ScreenMatch,
            page=page,
        )
        write_match_artifact(services, "image", response)
        response = local_response(response, screen)
        response = omit_nonblocking_image_request(services, response, screen)
        if response.needs_image:
            raise ValueError("screen_match_unresolved_image")
    response = bind_value_evidence_to_controls(services, response, screen)
    return MatchSelection(response, cache_hit, method_proposal, used_image)


def validate_or_replan(
    response: ScreenMatch,
    request: MatchRequest,
    services: MatchServices,
    prepared: MatchPromptContext,
) -> ScreenMatch:
    """Validate one task plan and repair only a re-plannable action error."""

    active_fields = set(prepared.active_fields)
    try:
        return prepare_match_plan(request, services, response, prepared.capability, active_fields)
    except ValueError as error:
        if (
            services.settings.guard_mode == "observe_only"
            and str(error) not in _EXECUTOR_CONTRACT_ERRORS
        ):
            services.log.append(
                "verify",
                payload={
                    "check": "guard_observe_only",
                    "guard": "full_plan_correction_call",
                    "outcome": "disabled",
                    "error": str(error),
                },
            )
            raise
        if str(error) not in _REPLANNABLE_MATCH_ERRORS:
            raise
        if str(error) == "screen_match_action_surface_mismatch":
            correction_state = {
                **prepared.state,
                "correction": {
                    "error": str(error),
                    "instruction": (
                        "Re-read the CURRENT screen and return a corrected interpretation and "
                        "plan. For every step, choose a target marker whose allowed_actions "
                        "contains the requested action. OCR markers may only be click targets "
                        "when their visible text is the requested tab, button, menu, link, row, "
                        "or cell; "
                        "never fill or send keys to OCR. Keep controls and steps target IDs "
                        "consistent."
                    ),
                    "rejected_target_ids": sorted(step.target for step in response.steps),
                },
            }
            corrected = _call_match(
                services,
                "replan_after_rejection",
                TASK_RULES
                + prepared.guard_rules
                + prepared.taught_rules
                + prepared.method_rules
                + json.dumps(correction_state, ensure_ascii=False),
                ScreenMatch,
                page=request.page,
            )
            corrected = local_response(corrected, request.screen)
            corrected = bind_value_evidence_to_controls(services, corrected, request.screen)
            corrected = prepare_match_plan(
                request, services, corrected, prepared.capability, active_fields
            )
            write_match_artifact(services, "action-surface-correction", corrected)
            services.log.append(
                "verify",
                payload={
                    "check": "action_surface_corrected",
                    "rejected_target_ids": sorted(step.target for step in response.steps),
                    "corrected_target_ids": sorted(step.target for step in corrected.steps),
                },
            )
            return corrected
        if str(error) == "screen_match_action_target_uncertain":
            correction_state = {
                **prepared.state,
                "correction": {
                    "error": str(error),
                    "instruction": (
                        "The proposed action target will be disabled by local visual confidence. "
                        "Re-read the whole CURRENT screen and choose another confidently grounded "
                        "action. If the selected taught step's expected result is already visible, "
                        "advance to the next applicable taught step instead of repeating it."
                    ),
                    "rejected_target_ids": sorted(step.target for step in response.steps),
                },
            }
            corrected = _call_match(
                services,
                "replan_after_rejection",
                TASK_RULES
                + prepared.guard_rules
                + prepared.taught_rules
                + prepared.method_rules
                + json.dumps(correction_state, ensure_ascii=False),
                ScreenMatch,
                page=request.page,
            )
            corrected = local_response(corrected, request.screen)
            corrected = bind_value_evidence_to_controls(services, corrected, request.screen)
            corrected = prepare_match_plan(
                request, services, corrected, prepared.capability, active_fields
            )
            write_match_artifact(services, "uncertain-target-correction", corrected)
            services.log.append(
                "verify",
                payload={
                    "check": "uncertain_action_target_corrected",
                    "rejected_target_ids": sorted(step.target for step in response.steps),
                    "corrected_target_ids": sorted(step.target for step in corrected.steps),
                },
            )
            return corrected
        interpretation = response.interpretation()
        correction_instruction = (
            "Keep grounded_interpretation unchanged. Return a task plan that matches the "
            "supplied capability and the execution contract."
        )
        if str(error) == "screen_match_step_not_executable":
            correction_instruction += (
                " Every click or double_click step MUST include an original-image [x,y] point "
                "inside its intended CURRENT visible target. Every fill step MUST use "
                "action=fill, intent=fill or credential, and a notebook input key as source."
            )
        elif str(error) == "screen_match_model_point_required":
            correction_instruction += (
                " Every step targeting a model-created control ID MUST include an "
                "original-image [x,y] point inside that CURRENT visible control, including fill."
            )
        elif str(error) == "screen_match_completion_decision_required":
            correction_instruction += (
                " Runtime evidence reports that all required inputs have been verified. Do not "
                "stop with work described as in progress. Re-evaluate the CURRENT visible proof "
                "and either return operation=complete with completion_evidence mapped to a user "
                "completion condition, or return the one executable action still required."
            )
        correction_state = {
            **prepared.state,
            "grounded_interpretation": {
                "screen_kind": interpretation.screen_kind,
                "controls": model_controls(interpretation.controls, request.screen),
                "uncertainties": interpretation.uncertainties,
            },
            "correction": {
                "error": str(error),
                "instruction": correction_instruction,
            },
        }
        plan = _call_match(
            services,
            "replan_after_rejection",
            TASK_PLAN_RULES
            + prepared.guard_rules
            + prepared.taught_rules
            + prepared.method_rules
            + json.dumps(correction_state, ensure_ascii=False),
            ScreenPlan,
            page=request.page,
        )
        corrected = ScreenMatch.model_validate({**interpretation.model_dump(), **plan.model_dump()})
        corrected = prepare_match_plan(
            request, services, corrected, prepared.capability, active_fields
        )
        write_match_artifact(services, "action-correction", corrected)
        services.log.append(
            "verify",
            payload={
                "check": "combined_action_corrected",
                "error": str(error),
                "procedure_step": prepared.capability.capability_id,
            },
        )
        return corrected


def ground_match_response(
    response: ScreenMatch,
    request: MatchRequest,
    services: MatchServices,
    prepared: MatchPromptContext,
    *,
    used_image: bool,
) -> ScreenMatch:
    """Ground referenced controls and revalidate writes without hidden model retries."""

    screen, page = request.screen, request.page
    required = request.required_verification
    response = omit_nonblocking_refinement(services, response, screen)
    if not response.needs_refinement:
        response = sanitize_unused_controls(services, request.phase, response, screen, required)
        response = retain_action_targets(services, response, screen, page)
        response = retain_verification(
            services,
            response,
            screen,
            required,
            page if used_image else None,
        )
        response = sanitize_unused_controls(services, request.phase, response, screen, required)
        if response.operation == "act" and not response.steps:
            if services.settings.guard_mode == "observe_only":
                services.log.append(
                    "verify",
                    payload={
                        "check": "guard_observe_only",
                        "guard": "empty_action_completion_call",
                        "outcome": "disabled",
                    },
                )
                raise ValueError("screen_match_empty_action")
            else:
                completion = _call_match(
                    services,
                    "missing_actions_recovery",
                    "The current form was matched but operation=act had no executable steps. "
                    "Return only the missing steps for this procedure goal using these CURRENT "
                    "control IDs and source keys. Never infer that an action already happened. "
                    "If no valid action is available, return operation=stop and steps=[].\n"
                    + json.dumps(
                        {
                            "capability": prepared.capability.model_dump(),
                            "notebook": prepared.state["notebook"],
                            "execution": prepared.state["execution"],
                            "screen": prepared.state["screen"],
                            "controls": model_controls(response.controls, screen),
                        },
                        ensure_ascii=False,
                    ),
                    ActionCompletion,
                    page=page if used_image else None,
                )
                response = response.model_copy(
                    update={"operation": completion.operation, "steps": completion.steps}
                )
                services.log.append(
                    "verify",
                    payload={
                        "check": "missing_match_actions_completed",
                        "operation": response.operation,
                        "steps": len(response.steps),
                    },
                )
    response = prepare_match_plan(
        request,
        services,
        response,
        prepared.capability,
        set(prepared.active_fields),
    )
    if not response.needs_refinement:
        response = retain_action_targets(services, response, screen, page)
        response = sanitize_unused_controls(services, request.phase, response, screen, required)
    return response


def match_screen(request: MatchRequest, services: MatchServices) -> MatchResult:
    screen = request.screen
    decision_id = uuid4().hex
    original_call = services.call

    def traced_call(
        prompt: str, schema: type[ResponseT], *, page: RenderedPage | None = None
    ) -> ResponseT:
        with model_call_context(
            decision_id=decision_id,
            frame=str(request.page.image_path) if request.page else None,
            phase=request.phase,
        ):
            return original_call(prompt, schema, page=page)

    services = replace(services, call=traced_call)
    services.log.append(
        "select",
        payload={
            "check": "screen_decision_started",
            "decision_id": decision_id,
            "frame": str(request.page.image_path) if request.page else None,
        },
    )
    prepared = build_match_prompt(request, services)
    state = prepared.state
    capability = prepared.capability
    prompt = prepared.prompt
    selection = select_match_response(request, services, prepared)
    response = selection.response
    cache_hit = selection.cache_hit
    method_proposal = selection.method_proposal
    used_image = selection.used_image
    reported_response = response
    try:
        response = validate_or_replan(response, request, services, prepared)
        response = ground_match_response(
            response,
            request,
            services,
            prepared,
            used_image=used_image,
        )
    except ValueError as error:
        if method_proposal is None:
            raise
        rejected = services.methods.reject_pending(reason=str(error))
        services.log.append(
            "verify",
            payload={
                "check": "method_fallback",
                "method_id": None if rejected is None else rejected["method_id"],
                "group_id": None if rejected is None else rejected["group_id"],
                "reason": str(error)[:160],
                "next": "visual_cache_or_model",
            },
        )
        # Rebuild method memory while keeping the caller-owned education focus.
        # A failed cache binding must not reset or invent teaching progress.
        fallback_request = replace(
            request,
            proposal=None,
        )
        prepared = build_match_prompt(fallback_request, services)
        state = prepared.state
        selection = select_match_response(fallback_request, services, prepared, allow_method=False)
        response = selection.response
        reported_response = response
        cache_hit = selection.cache_hit
        method_proposal = None
        used_image = selection.used_image
        response = validate_or_replan(response, fallback_request, services, prepared)
        response = ground_match_response(
            response,
            fallback_request,
            services,
            prepared,
            used_image=used_image,
        )
    observation_claim = None
    if reported_response.operation != "act" and reported_response.steps:
        observation_claim = {
            "operation": reported_response.operation,
            "grid": reported_response.grid,
            "row": reported_response.row,
            "evidence": [item.model_dump(mode="json") for item in reported_response.evidence],
            "frame": str(request.page.image_path) if request.page else None,
            "planned_operation": response.operation,
        }
        services.log.append(
            "verify",
            payload={
                "check": "observation_claim_preserved",
                "status": "reported_not_independently_verified",
                **observation_claim,
            },
        )
    services.log.append(
        "verify",
        payload={
            "check": "match_context_size",
            "frame": str(request.page.image_path) if request.page else None,
            "sections_utf8_bytes": {
                key: len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
                for key, value in prepared.state.items()
            },
            "selection_source": "method"
            if method_proposal is not None
            else "visual_cache"
            if cache_hit is not None
            else "model",
        },
    )
    visual_match_candidate = (
        (
            cache_hit[0] if cache_hit is not None else visual_match_key(state),
            response.model_dump(mode="json"),
        )
        if method_proposal is None
        else None
    )
    services.log.append(
        "select",
        payload={
            "check": "screen_mapping",
            "decision_id": decision_id,
            "frame": str(request.page.image_path) if request.page else None,
            "phase": request.phase,
            "procedure_step": capability.capability_id,
            "steps": len(response.steps),
            "expected_result": response.expected_result,
            "purpose": response.purpose,
            "method_id": response.method_id,
            "prompt_chars": len(prompt),
        },
    )
    if services.methods.enabled and response != method_proposal:
        services.methods.remember(screen, response)
    return MatchResult(
        response=response,
        visual_match_candidate=visual_match_candidate,
        observation_claim=observation_claim,
    )


def compile_match(response: ScreenMatch, observation, schema):
    def ref(target):
        return None if target is None else f"m{target}"

    def decision(action=None):
        return {
            "decision": Decision(
                screen_kind=response.screen_kind,
                operation=response.operation,
                intent=action.intent
                if action
                else "stop"
                if response.operation == "stop"
                else "verify",
                subgoal=response.purpose or response.procedure_step,
                reason=response.expected_result or "Current form matched to procedure",
                action=Choice(
                    type="set_text" if action.action == "fill" else action.action,
                    ref=ref(action.target),
                    source=action.source if action.action == "fill" else None,
                    keys=action.keys,
                )
                if action
                else None,
                grid_ref=ref(response.grid)
                if response.operation in {"duplicate_clear", "duplicate_found", "complete"}
                else None,
                row_ref=ref(response.row) if response.operation == "complete" else None,
                evidence=[{"field": e.field, "ref": ref(e.target)} for e in response.evidence]
                if response.operation in {"act", "complete"}
                else [],
                completion_evidence=[
                    {"condition_index": e.condition_index, "ref": ref(e.target)}
                    for e in response.completion_evidence
                ]
                if response.operation == "complete"
                else [],
            ).model_dump(exclude_none=True, exclude_defaults=True)
        }

    fills = [s for s in response.steps if s.action == "fill"]
    credentials = [s for s in fills if (s.source or "").startswith("credential.")]
    if response.operation != "act" and response.steps:
        raise ValueError("screen_match_proof_with_actions")
    if credentials:
        if len(credentials) != len(fills) or not _is_login_batch(response.steps):
            raise ValueError("screen_match_invalid_login_batch")
        command = response.steps[-1]
        data = {
            "fields": [{"source": s.source, "ref": ref(s.target)} for s in fills],
            "login_ref": ref(command.target),
            "next_decision": None,
        }
    elif (
        fills
        and all(s.intent == "fill" for s in fills)
        and (response.screen_kind == "editor" or response.procedure_step == "task-action")
    ):
        if len(fills) != len(response.steps):
            raise ValueError("screen_match_mixed_field_batch")
        data = {
            "fields": [{"source": s.source, "ref": ref(s.target)} for s in fills],
            "purpose": response.purpose,
            "next_decision": None,
        }
    else:
        if fills and len(response.steps) > 1:
            raise ValueError("screen_match_mixed_field_batch")
        if len(response.steps) > 1 or (response.operation == "act" and not response.steps):
            raise ValueError("screen_match_requires_one_boundary_action")
        data = decision(response.steps[0] if response.steps else None)
    if response.save_result is not None:
        data = {
            "result": response.save_result,
            "evidence_refs": [ref(i) for i in response.message_targets],
            "repair_fields": response.repair_fields,
            "next_decision": data,
        }

    # Reuse existing schemas, current-ref grounding and the common executor.
    class Payload(BaseModel):
        model_config = ConfigDict(extra="allow")

    bound = bind_proposal(
        Payload.model_validate(data),
        "e" + str(observation.obs_id.int) + "0",
        {e.ref for e in observation.elements},
    )
    return adapt_proposal(bound, schema)


def compile_selection_plan(response: ScreenMatch, observation):
    """Compile row selection and confirmation through the existing action executor."""
    from .execution import ActionPlan, PlannedAction
    from .schemas import DecisionResponse

    if response.operation != "act" or not response.steps:
        return None
    if not all(s.intent == "select_option" and s.action == "click" for s in response.steps):
        return None
    if response.steps[-1].source is None or response.steps[-1].source.startswith("credential."):
        raise ValueError("selection_effect_source_required")
    actions = tuple(
        PlannedAction(
            compile_match(
                response.model_copy(update={"steps": [step]}), observation, DecisionResponse
            ).to_decision(),
            effect_source=step.source if index == len(response.steps) - 1 else None,
        )
        for index, step in enumerate(response.steps)
    )
    return ActionPlan(
        actions,
        "single",
        {
            "check": "selection_screen_plan",
            "actions": len(actions),
        },
    )


def compile_screen_action_plan(response: ScreenMatch, observation):
    """Compile same-screen preparation clicks before one navigation boundary."""
    from .execution import ActionPlan, PlannedAction
    from .schemas import DecisionResponse

    if response.operation != "act" or len(response.steps) <= 1:
        return None
    if all(step.intent == "select_option" for step in response.steps):
        return None
    intents = {step.intent for step in response.steps}
    selection_then_navigation = response.steps[-1].intent == "navigate" and all(
        step.intent == "select_option" for step in response.steps[:-1]
    )
    if (intents not in ({"navigate"}, {"select_tab"}) and not selection_then_navigation) or any(
        step.action not in {"click", "double_click", "key"} or step.source is not None
        for step in response.steps
    ):
        return None
    actions = tuple(
        PlannedAction(
            compile_match(
                response.model_copy(update={"steps": [step]}), observation, DecisionResponse
            ).to_decision()
        )
        for step in response.steps
    )
    return ActionPlan(
        actions,
        "single",
        {
            "check": "same_screen_action_plan",
            "actions": len(actions),
            "intent": "select_then_navigate" if selection_then_navigation else next(iter(intents)),
        },
    )
