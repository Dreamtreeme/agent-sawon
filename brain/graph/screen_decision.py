"""Screen decision: recall execution context, match patterns, then select a plan."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from brain.cache import ScreenPatternCache, screen_pattern_key
from brain.input_binding import (
    allowed_input_surfaces,
    normalize_action_source,
    resolve_input_key,
)
from brain.observe.evidence import input_surface
from brain.settings import BrainSettings
from brain.task_instruction import ObservedStepResult, TaskInstruction
from brain.visual.frame import frame_input
from brain.visual.markers import allowed_marker_actions, marker_role_allowed
from brain.visual.scene import ControlMeaning, SemanticDetection, contains, overlap
from brain.vocabulary import (
    ACTION_CONTROL_ROLES,
    SCROLL_DIRECTION_KEYS,
    ActionCapabilityIntent,
    KeyInput,
    MatchActionIntent,
)
from models.base import model_call_context
from models.schema import compact_provider_schema
from sawon_runtime.storage import RunLog, atomic_write_json, rejection_evidence
from sawon_runtime.visual import RenderedPage

from .context_recall import recall_context
from .execution import GuardStop, PendingAction
from .model_call import ResponseT, ScreenModelCall
from .policy import DecisionPolicy
from .schemas import Choice, Decision, FieldName
from .screen_mapping import ScreenMapping, ScreenMappingWire, mapping_prompt
from .screen_memory import pending_screen_inputs
from .visual_turn import adapt_proposal


class ActionCapability(BaseModel):
    """The one runtime action envelope for the current user task."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    capability_id: Literal["task-action"] = "task-action"
    input_scope: list[FieldName] = Field(default_factory=list)
    actions: list[ActionCapabilityIntent] = Field(default_factory=list)
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
    intent: MatchActionIntent
    keys: KeyInput | None = None
    point: list[float] | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description=(
            "Original-image [x,y] point inside target, except pagedown/pageup wheel points "
            "which may be anywhere in the current image's scrollable region. "
            "Required for click/double_click and for "
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
    resume_group_id: str | None = None
    method_bindings: list[MethodBinding] = Field(default_factory=list, max_length=100)


class ScreenPlan(MethodChoice):
    """Task decision over a previously grounded screen interpretation."""

    anthropic_unions: ClassVar[dict[str, dict[str, Any]]] = ScreenMappingWire.anthropic_unions
    anthropic_nulls: ClassVar[dict[str, Any]] = ScreenMappingWire.anthropic_nulls

    model_config = ConfigDict(extra="forbid")
    mapping: ScreenMapping | None = Field(default=None, exclude=True)
    needs_refinement: bool = False

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        if kwargs.get("mode", "validation") != "serialization":
            return compact_provider_schema(ScreenMappingWire.model_json_schema())
        return super().model_json_schema(*args, **kwargs)

    @model_validator(mode="before")
    @classmethod
    def semantic_mapping(cls, value):
        if isinstance(value, dict) and "fields" in value:
            mapping = ScreenMapping.model_validate(value)
            # Parse model semantics now; allocate execution refs once the frame is available.
            payload = {"procedure_step": mapping.procedure_step, "mapping": mapping}
            if "screen_kind" in cls.model_fields:
                payload["screen_kind"] = mapping.screen_kind
                payload["controls"] = []
            return payload
        return value

    procedure_step: str
    observed_step_results: list[ObservedStepResult] = Field(default_factory=list)
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
    def from_plan(cls, interpretation: SemanticDetection, plan: ScreenPlan) -> ScreenMatch:
        """New plan fields win, including any full interpretation returned by the model."""
        return cls.model_validate({**interpretation.model_dump(), **plan.model_dump()}).model_copy(
            update={"mapping": plan.mapping}
        )  # mapping is excluded from model_dump.

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

    def accepted_mapping(self, screen: dict) -> ScreenMapping | None:
        """Keep observations intact while persisting the plan the executor actually accepted."""
        if self.mapping is None:
            return None
        names = {
            control.id: screen.get("selected_fields", {})
            .get(str(control.id), {})
            .get("name", control.visual_description)
            for control in self.controls
        }
        directions = {key: direction for direction, key in SCROLL_DIRECTION_KEYS.items()}
        steps = []
        for step in self.steps:
            data = step.model_dump(exclude_none=True)
            data["target"] = names[step.target]
            if step.action == "key" and step.keys in directions:
                data.update(action="scroll", direction=directions[data.pop("keys")])
                data.pop("source", None)
            steps.append(data)
        return ScreenMapping.model_validate(
            {
                **self.mapping.model_dump(exclude_unset=True),
                **self.model_dump(
                    include={
                        "operation",
                        "requested_instruction_ids",
                        "taught_step_id",
                        "method_id",
                        "resume_group_id",
                        "needs_refinement",
                        "observed_step_results",
                        "save_result",
                        "repair_fields",
                    }
                ),
                "steps": steps,
                "completion_evidence": [
                    {"condition_index": item.condition_index, "target": names[item.target]}
                    for item in self.completion_evidence
                ],
                "message_targets": [names[target] for target in self.message_targets],
                "grid": names[self.grid] if self.grid is not None else None,
                "row": names[self.row] if self.row is not None else None,
            }
        )


class MemoryRetriever(Protocol):
    """Memory operations used by screen decisions, independent of their implementation."""

    enabled: bool

    def prompt_context(self) -> dict: ...

    def match_pattern(self, screen: dict) -> ScreenMatch | None: ...

    def remember(
        self, screen: dict, response: ScreenMatch, *, allow_recovery: bool = True
    ) -> None: ...

    def reject_pending(self, *, reason: str) -> dict | None: ...


@dataclass(frozen=True)
class ScreenDecisionRequest:
    """Immutable state needed to choose one action boundary for one screen."""

    screen: dict
    page: Any
    phase: str
    values: Mapping[str, str]
    verified_fields: frozenset[str]
    execution_contract: dict
    required_verification: tuple[dict, ...]
    post_action_review: bool
    workflow_flags: Mapping[str, bool]
    context_layers: dict
    recent_feedback: tuple[dict, ...]
    available_credentials: tuple[str, ...]
    failed_transitions: tuple[dict, ...]
    saved_record_review: dict | None
    taught_procedure_progress: dict
    task_instruction: TaskInstruction
    task_instruction_sha256: str
    policy: DecisionPolicy
    grounded_interpretation: SemanticDetection | None = None
    proposal: ScreenMatch | None = None
    screen_nodes: Mapping[str, dict] = field(default_factory=dict)
    current_screen_path: list[str] = field(default_factory=list)

    @cached_property
    def evidence_scope(self) -> str:
        return (
            "saved_detail"
            if self.workflow_flags.get("post_save_detail_review")
            else "saved_detail_candidate"
            if self.workflow_flags.get("saved_result_open_pending")
            else "entry"
        )


@dataclass(frozen=True)
class ScreenDecisionServices:
    """Explicit side-effect boundary used by screen decisions."""

    settings: BrainSettings
    log: RunLog
    call: ScreenModelCall
    case_dir: Path
    cache: ScreenPatternCache
    memory_retrieval: MemoryRetriever


@dataclass(frozen=True)
class ScreenDecisionResult:
    """Screen decision plus the two state updates owned by the graph runtime."""

    response: ScreenMatch
    visual_match_candidate: tuple[str, dict] | None
    observation_claim: dict | None = None
    plan_error: str | None = None


@dataclass(frozen=True)
class DecisionPromptContext:
    state_factory: Callable[[], dict]
    capability: ActionCapability
    active_fields: frozenset[str]

    @cached_property
    def state(self) -> dict:
        return self.state_factory()

    @cached_property
    def cache_key(self) -> str:
        return screen_pattern_key(self.state)


@dataclass(frozen=True)
class ScreenSelection:
    response: ScreenMatch
    cache_hit: tuple[str, dict] | None
    method_proposal: ScreenMatch | None
    used_image: bool
    prepared: DecisionPromptContext


def _call_screen_decision(
    services: ScreenDecisionServices,
    purpose: str,
    prompt: str,
    schema: type[ResponseT],
    *,
    page: RenderedPage | None = None,
) -> ResponseT:
    with model_call_context(purpose=purpose):
        return services.call(prompt, schema, page=page)


def write_decision_artifact(services: ScreenDecisionServices, kind: str, value: BaseModel) -> None:
    atomic_write_json(services.case_dir / "matching" / f"{uuid4().hex}-{kind}.json", value)


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
    services: ScreenDecisionServices,
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


def retain_screen_observation(
    response: ScreenMatch,
    request: ScreenDecisionRequest,
    services: ScreenDecisionServices,
    prepared: DecisionPromptContext,
    *,
    page=None,
) -> ScreenMatch:
    """Recover an incomplete observation through the one named decision contract."""
    required = list(request.required_verification)

    def missing_verification(candidate):
        absent, _ = verification_requirements(
            candidate.controls,
            request.screen,
            required,
            candidate.screen_kind,
            services.settings.visual_min_confidence,
        )
        if services.settings.visual_model_led_semantics:
            controls = {control.id: control for control in candidate.controls}
            explicit = {
                evidence.field
                for evidence in candidate.evidence
                if (
                    (control := controls.get(evidence.target)) is not None
                    and control.enabled
                    and control.role in {"edit", "combobox"}
                    and (
                        control.confidence_policy != "threshold"
                        or control.confidence >= services.settings.visual_min_confidence
                    )
                )
            }
            # Current explicit bindings survive resize; literal value verification
            # still happens before execution, as it did before contract unification.
            absent = [item for item in absent if item["source"] not in explicit]
        return absent

    if services.settings.visual_model_only_review:
        absent = []
        services.log.append(
            "verify",
            payload={
                "check": "required_verification_delegated",
                "reviewer": "screen_model",
                "sources": sorted(item["source"] for item in required),
            },
        )
    else:
        absent = missing_verification(response)
    targets = (
        {step.target for step in response.steps}
        | {item.target for item in response.evidence}
        | {item.target for item in response.completion_evidence}
    )
    missing = targets - {control.id for control in response.controls}
    if not absent and not missing:
        return response
    recovered = _call_screen_decision(
        services,
        "target_grounding" if absent else "input_evidence_recovery",
        mapping_prompt(
            {
                **prepared.state,
                "recovery": {
                    "reason": "incomplete_observation",
                    "instruction": (
                        "Return the current named fields, their source keys and observed values, "
                        "and remaining actions together. Include every action target in fields. "
                        "Do not infer current values from past memory or repeat satisfied inputs."
                    ),
                    "required_sources": [item["source"] for item in absent],
                    "current_observation": (
                        response.mapping.model_dump(exclude_unset=True)
                        if response.mapping
                        else None
                    ),
                },
            }
        ),
        ScreenMatch,
        page=page,
    )
    if not services.settings.visual_model_only_review and missing_verification(recovered):
        raise GuardStop("required_verification_missing")
    recovered_targets = (
        {step.target for step in recovered.steps}
        | {item.target for item in recovered.evidence}
        | {item.target for item in recovered.completion_evidence}
    )
    if recovered_targets - {control.id for control in recovered.controls}:
        raise GuardStop("action_target_semantics_missing")
    write_decision_artifact(services, "observation-recovery", recovered)
    return recovered


def bind_value_evidence_to_controls(
    services: ScreenDecisionServices, response: ScreenMatch, screen: dict
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


def _validate_taught_step(response: ScreenMatch, request: ScreenDecisionRequest) -> None:
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


def _validate_saved_row_navigation_target(
    response: ScreenMatch, request: ScreenDecisionRequest
) -> None:
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
    response: ScreenMatch, request: ScreenDecisionRequest, services: ScreenDecisionServices
) -> ScreenMatch:
    """Normalize provider vocabulary at the model boundary."""

    normalized = []
    for item in response.steps:
        if (
            request.phase == "task"
            and item.action == "fill"
            and item.source is not None
            and not item.source.startswith("credential.")
        ):
            item = item.model_copy(update={"intent": "fill"})
        elif item.action != "fill" and item.intent in {"fill", "query_input", "credential"}:
            item = item.model_copy(update={"intent": "navigate", "source": None})
            services.log.append(
                "verify",
                payload={
                    "check": "nonwriting_fill_intent_normalized",
                    "action": item.action,
                    "keys": item.keys,
                    "target": item.target,
                    "intent": "navigate",
                },
            )
        # Resolve the concrete write action before pruning sources by intent;
        # a fill described as "search" must not lose its supplied input key.
        source = normalize_action_source(
            intent=item.intent,
            candidate=item.source,
            inputs=request.values,
        )
        normalized.append(item.model_copy(update={"source": source}))
    evidence = [
        item.model_copy(
            update={"field": resolve_input_key(item.field, request.values) or item.field}
        )
        for item in response.evidence
    ]
    return response.model_copy(update={"steps": normalized, "evidence": evidence})


def _validate_completion_annotations(
    response: ScreenMatch, request: ScreenDecisionRequest, services: ScreenDecisionServices
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


def _validate_login_boundary(response: ScreenMatch, request: ScreenDecisionRequest) -> None:
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
    response: ScreenMatch, request: ScreenDecisionRequest, services: ScreenDecisionServices
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


def _validate_action_boundary(
    response: ScreenMatch,
    capability: ActionCapability,
    request: ScreenDecisionRequest,
) -> None:
    allowed_intents = set(capability.actions)
    if "login" in allowed_intents:
        allowed_intents.add("credential")
    if request.phase != "save_result" and any(
        action.intent not in allowed_intents for action in response.steps
    ):
        raise ValueError("screen_match_action_outside_procedure")


def _validate_input_scope(response: ScreenMatch, capability: ActionCapability) -> None:
    scoped_sources = {
        action.source
        for action in response.steps
        if action.source is not None and not action.source.startswith("credential.")
    }
    if capability.input_scope and not scoped_sources <= set(capability.input_scope):
        raise ValueError("screen_match_field_outside_procedure_scope")


def _validate_action_targets_executable(
    response: ScreenMatch, services: ScreenDecisionServices
) -> None:
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
    request: ScreenDecisionRequest,
    services: ScreenDecisionServices,
    active_fields: set[str],
) -> ScreenMatch:
    """Validate task actions separately from an already grounded screen interpretation."""
    with rejection_evidence(
        services.log,
        "screen_decision.validate_match_plan",
        response=response,
        context={"phase": request.phase},
    ):
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
                (
                    "action_boundary",
                    lambda: _validate_action_boundary(response, capability, request),
                ),
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


def validate_action_surfaces(
    services: ScreenDecisionServices, response: ScreenMatch, screen: dict
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
            and control.role not in ACTION_CONTROL_ROLES[step.action]
        ):
            reason = "role_not_actionable"
        if reason == "role_not_actionable" and services.settings.guard_mode == "observe_only":
            services.log.append(
                "verify",
                payload={
                    "check": "guard_observe_only",
                    "guard": "action_control_role",
                    "target": step.target,
                    "role": control.role,
                    "action": step.action,
                },
            )
            continue
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
    request: ScreenDecisionRequest, services: ScreenDecisionServices, response: ScreenMatch
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
            # Missing action fields are recovered by the shared observation path.
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


def prepare_screen_plan(
    request: ScreenDecisionRequest,
    services: ScreenDecisionServices,
    response: ScreenMatch,
    capability: ActionCapability,
    active_fields: set[str],
):
    """Compile every proposal through the same task and write-surface contract."""
    with rejection_evidence(
        services.log,
        "screen_decision.prepare_screen_plan",
        response=response,
        context={"screen": request.screen, "phase": request.phase},
    ):
        response = response.model_copy(
            update={
                "purpose": (response.purpose or response.procedure_step)[:160],
                "procedure_step": capability.capability_id,
            }
        )
        if (
            request.phase == "task"
            and response.operation in {"duplicate_clear", "duplicate_found"}
            and response.steps
        ):
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


def build_decision_prompt(
    request: ScreenDecisionRequest, services: ScreenDecisionServices
) -> DecisionPromptContext:
    """Prepare execution eligibility; assemble model context only when it is consumed."""

    contract = request.execution_contract
    active_fields = set(request.values)
    capability = task_action_contract(
        request.phase, request.task_instruction, contract, active_fields
    )
    # Pattern binding can mutate these two sources. Preserve their original
    # snapshot timing so later recovery prompts and cache keys stay unchanged.
    screen = frame_input(request.screen)
    methods = services.memory_retrieval
    method_memory = methods.prompt_context() if methods.enabled else None

    def assemble_state():
        state = recall_context(request, projected_screen=screen)
        state["capability"] = capability.model_dump()
        if contract["conditional"]:
            state["proposal_after_verification"] = {
                "condition": contract["condition"],
                "checkpoint": contract["checkpoint"],
            }
        if "save_review" in contract:
            state["save_review"] = contract["save_review"]
        if method_memory is not None:
            state["method_memory"] = method_memory
        return state

    return DecisionPromptContext(
        state_factory=assemble_state, capability=capability, active_fields=frozenset(active_fields)
    )


def match_screen_pattern(
    request: ScreenDecisionRequest,
    services: ScreenDecisionServices,
    prepared: DecisionPromptContext,
    *,
    allow_method: bool = True,
) -> tuple[ScreenMatch | None, tuple[str, dict] | None]:
    """Bind an existing procedure or cached plan to current context before model selection."""
    screen = request.screen
    method_proposal = request.proposal if allow_method else None
    if allow_method and method_proposal is None and "correction" not in screen:
        method_proposal = services.memory_retrieval.match_pattern(screen)
        if (
            request.phase == "save_result"
            and method_proposal is not None
            and (
                method_proposal.save_result in {None, "unknown"}
                or not method_proposal.message_targets
            )
        ):
            # Legacy/action-only methods still need current result evidence.
            # A method carrying a rebound save review uses the common executor.
            services.log.append(
                "verify",
                payload={
                    "check": "memory_retrieval_exception",
                    "reason": "save_result_not_bound",
                },
            )
            method_proposal = None
    cache_hit = (
        services.cache.lookup(prepared.cache_key)
        if method_proposal is None and services.cache.enabled and request.phase != "save_result"
        else None
    )
    return method_proposal, cache_hit


def select_screen_response(
    request: ScreenDecisionRequest,
    services: ScreenDecisionServices,
    prepared: DecisionPromptContext,
    *,
    allow_method: bool = True,
) -> ScreenSelection:
    """Resolve method, cache, grounded-plan and model sources in priority order."""

    screen, page = request.screen, request.page
    current_review = bool(
        request.post_action_review
        or request.workflow_flags.get("saved_result_open_pending")
        or any(
            item["source"] in request.values and item["source"] not in request.verified_fields
            for item in request.required_verification
        )
    )
    if current_review:
        method_proposal, cache_hit = None, None
    else:
        method_proposal, cache_hit = match_screen_pattern(
            request, services, prepared, allow_method=allow_method
        )
        current_sources = {item.get("source") for item in screen.get("memory_fields", [])}
        if method_proposal is not None and method_proposal.mapping is not None:
            current_sources.update(
                field.source for field in method_proposal.mapping.fields.values()
            )
        elif cache_hit is not None:
            current_sources.update(
                field.get("source")
                for field in (cache_hit[1].get("mapping") or {}).get("fields", {}).values()
            )
        current_review = bool(
            pending_screen_inputs(request.screen_nodes, request.evidence_scope) & current_sources
            or (method_proposal is not None and method_proposal.operation == "complete")
            or (cache_hit is not None and cache_hit[1].get("operation", "act") != "act")
            or (request.phase == "save_result" and method_proposal is None)
        )
        if current_review:
            method_proposal, cache_hit = None, None
    if current_review:
        # Stored structure chooses actions, never supplies readings for this run.
        # One current mapping also plans the next action after reading the result.
        services.log.append(
            "verify",
            payload={
                "check": "memory_retrieval_exception",
                "reason": "current_values_not_observed",
            },
        )
    services.log.append(
        "select",
        payload={
            "check": "screen_pattern_selection",
            "observation_route": screen.get("observation_route"),
            "source": (
                "method"
                if method_proposal is not None
                else "screen_cache"
                if cache_hit is not None
                else "grounded_plan"
                if request.grounded_interpretation is not None and not current_review
                else "model"
            ),
        },
    )
    if method_proposal is not None:
        response = method_proposal
    elif cache_hit is not None:
        _, cached = cache_hit
        response = ScreenMatch.model_validate(cached)
        if response.mapping is not None:
            mapping = ScreenMapping.model_validate(
                {
                    **response.mapping.model_dump(exclude={"fields"}),
                    "fields": {
                        name: field.model_dump(exclude={"value"})
                        for name, field in response.mapping.fields.items()
                    },
                    "observed_step_results": [],
                    "resume_group_id": None,
                    "save_result": None,
                    "message_targets": [],
                    "repair_fields": [],
                    "completion_evidence": [],
                }
            )
            response = ScreenMatch.model_validate(
                mapping.local_payload(screen, request.values)
            ).model_copy(update={"mapping": mapping})
        services.log.append(
            "cache_hit",
            payload={
                "check": "visual_match_cache",
                "key": cache_hit[0],
                "procedure_step": response.procedure_step,
            },
        )
    elif request.grounded_interpretation is not None and not current_review:
        grounded = request.grounded_interpretation
        plan = _call_screen_decision(
            services,
            "grounded_screen_plan",
            mapping_prompt(prepared.state),
            ScreenPlan,
            page=page,
        )
        response = ScreenMatch.from_plan(grounded, plan)
        write_decision_artifact(services, "cached-plan", response)
        services.log.append(
            "verify",
            payload={
                "check": "grounded_interpretation_planned",
                "controls": len(grounded.controls),
            },
        )
    else:
        if screen.get("observation_route") == "memory_retrieval" and not current_review:
            services.log.append(
                "verify",
                payload={
                    "check": "memory_retrieval_exception",
                    "reason": "no_unique_next_plan",
                },
            )
        response = _call_screen_decision(
            services,
            (
                "input_review"
                if request.post_action_review
                else "completion_review"
                if request.workflow_flags.get("save_issued")
                else "screen_decision"
            ),
            mapping_prompt(prepared.state),
            ScreenMatch,
            page=page,
        )
        write_decision_artifact(services, "markers", response)
    if (method_proposal is not None or cache_hit is not None) and response.mapping is None:
        services.log.append(
            "verify",
            payload={
                "check": "memory_retrieval_exception",
                "reason": "missing_screen_memory",
            },
        )
        response = _call_screen_decision(
            services,
            "grounded_screen_plan",
            mapping_prompt(prepared.state),
            ScreenMatch,
            page=page,
        )
        method_proposal = None
        cache_hit = None
    if response.requested_instruction_ids:
        by_id = {step.id: step for step in request.task_instruction.procedure}
        requested = list(dict.fromkeys(response.requested_instruction_ids))
        if any(step_id not in by_id for step_id in requested):
            raise ValueError("unknown_requested_instruction")
        supplied = {
            step["id"]: step for step in prepared.state["taught_procedure_progress"]["steps"]
        }
        missing = [step_id for step_id in requested if step_id not in supplied]
        if not missing:
            services.log.append(
                "verify",
                payload={
                    "check": "resolved_education_request_ignored",
                    "step_ids": requested,
                    "accepted_actions": len(response.steps),
                },
            )
            response = response.model_copy(update={"requested_instruction_ids": []})
    if response.requested_instruction_ids:
        supplied.update({step_id: by_id[step_id].model_dump(mode="json") for step_id in missing})
        expanded = {
            **prepared.state,
            "taught_procedure_progress": {
                **request.taught_procedure_progress,
                "steps": list(supplied.values()),
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
        prepared = replace(prepared, state_factory=lambda: expanded)
        response = _call_screen_decision(
            services,
            "education_retrieval",
            mapping_prompt(expanded),
            ScreenMatch,
            page=page,
        )
        write_decision_artifact(services, "education-plan", response)
        if response.requested_instruction_ids:
            if not response.steps:
                raise ValueError("education_retrieval_unresolved")
            services.log.append(
                "verify",
                payload={
                    "check": "resolved_education_request_ignored",
                    "step_ids": response.requested_instruction_ids,
                    "accepted_actions": len(response.steps),
                },
            )
            response = response.model_copy(update={"requested_instruction_ids": []})
        cache_hit = None
        method_proposal = None
    used_image = cache_hit is None and method_proposal is None
    if response.needs_image:
        response = _call_screen_decision(
            services,
            "image_recovery",
            mapping_prompt(prepared.state),
            ScreenMatch,
            page=page,
        )
        write_decision_artifact(services, "image", response)
        if response.needs_image:
            raise ValueError("screen_match_unresolved_image")
    if response.mapping is None:
        response = bind_value_evidence_to_controls(services, response, screen)
    return ScreenSelection(response, cache_hit, method_proposal, used_image, prepared)


def validate_or_replan(
    response: ScreenMatch,
    request: ScreenDecisionRequest,
    services: ScreenDecisionServices,
    prepared: DecisionPromptContext,
) -> ScreenMatch:
    """Validate one task plan and repair only a re-plannable action error."""

    active_fields = set(prepared.active_fields)
    try:
        return prepare_screen_plan(request, services, response, prepared.capability, active_fields)
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
        if str(error) in {
            "screen_match_action_surface_mismatch",
            "screen_match_action_target_uncertain",
        }:
            if str(error) == "screen_match_action_surface_mismatch":
                correction_instruction = (
                    "Re-read the CURRENT screen and return a corrected interpretation and "
                    "plan. For every step, name its actual visible field and control bbox. "
                    "A caption alone may be clicked but cannot serve as a writable input. "
                    "Fill and keyboard actions must target the actual input control. "
                    "Use the exact semantic field name in steps[].target."
                )
                artifact_kind = "action-surface-correction"
                correction_check = "action_surface_corrected"
            else:
                correction_instruction = (
                    "The proposed action target will be disabled by local visual confidence. "
                    "Re-read the whole CURRENT screen and choose another confidently grounded "
                    "action. If the selected taught step's expected result is already visible, "
                    "advance to the next applicable taught step instead of repeating it."
                )
                artifact_kind = "uncertain-target-correction"
                correction_check = "uncertain_action_target_corrected"
            correction_state = {
                **prepared.state,
                "correction": {
                    "error": str(error),
                    "instruction": correction_instruction,
                    "rejected_fields": [
                        control.visual_description
                        for control in response.controls
                        if control.id in {step.target for step in response.steps}
                    ],
                },
            }
            corrected = _call_screen_decision(
                services,
                "replan_after_rejection",
                mapping_prompt(correction_state),
                ScreenMatch,
                page=request.page,
            )
            if corrected.mapping is None:
                corrected = bind_value_evidence_to_controls(services, corrected, request.screen)
            corrected = prepare_screen_plan(
                request, services, corrected, prepared.capability, active_fields
            )
            write_decision_artifact(services, artifact_kind, corrected)
            services.log.append(
                "verify",
                payload={
                    "check": correction_check,
                    "rejected_target_ids": sorted(step.target for step in response.steps),
                    "corrected_target_ids": sorted(step.target for step in corrected.steps),
                },
            )
            return corrected
        interpretation = response.interpretation()
        correction_instruction = (
            "Return current named fields and a task plan matching the supplied capability "
            "and execution contract. Preserve current readings and correct the rejected action."
        )
        if str(error) == "screen_match_step_not_executable":
            correction_instruction += (
                " Every click or double_click step MUST include an original-image [x,y] point "
                "inside its intended CURRENT visible target. Every fill step MUST use "
                "action=fill, intent=fill or credential, and a notebook input key as source."
            )
        elif str(error) == "screen_match_model_point_required":
            correction_instruction += (
                " Every step MUST include an original-image [x,y] point inside its named "
                "CURRENT visible control, including fill."
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
            "correction": {
                "error": str(error),
                "instruction": correction_instruction,
            },
        }
        plan = _call_screen_decision(
            services,
            "replan_after_rejection",
            mapping_prompt(correction_state),
            ScreenPlan,
            page=request.page,
        )
        corrected = ScreenMatch.from_plan(interpretation, plan)
        corrected = prepare_screen_plan(
            request, services, corrected, prepared.capability, active_fields
        )
        write_decision_artifact(services, "action-correction", corrected)
        services.log.append(
            "verify",
            payload={
                "check": "combined_action_corrected",
                "error": str(error),
                "procedure_step": prepared.capability.capability_id,
            },
        )
        return corrected


def ground_screen_response(
    response: ScreenMatch,
    request: ScreenDecisionRequest,
    services: ScreenDecisionServices,
    prepared: DecisionPromptContext,
    *,
    used_image: bool,
) -> ScreenMatch:
    """Ground a validated plan; prepare again only when grounding/recovery changes it."""
    with rejection_evidence(
        services.log,
        "screen_decision.ground_screen_response",
        response=response,
        context={"screen": request.screen, "phase": request.phase},
    ):
        screen, page = request.screen, request.page
        required = request.required_verification
        validated_response = response
        grounded_response = response
        if not response.needs_refinement:
            response = retain_screen_observation(
                response, request, services, prepared, page=page if used_image else None
            )
            response = sanitize_unused_controls(services, request.phase, response, screen, required)
            grounded_response = response
            if response.operation == "act" and not response.steps:
                completion = _call_screen_decision(
                    services,
                    "missing_actions_recovery",
                    mapping_prompt(
                        {
                            **prepared.state,
                            "recovery": {
                                "reason": "missing_actions",
                                "instruction": (
                                    "The current observation is already available. Choose the next "
                                    "unfinished action using the user task and education. Do not "
                                    "repeat observed matching inputs or discard the current "
                                    "evidence."
                                ),
                                "matched_inputs": [item.field for item in response.evidence],
                                "current_observation": (
                                    response.mapping.model_dump(exclude_unset=True)
                                    if response.mapping is not None
                                    else None
                                ),
                            },
                        },
                    ),
                    ScreenMatch,
                    page=page if used_image else None,
                )
                response = completion
                services.log.append(
                    "verify",
                    payload={
                        "check": "missing_match_actions_completed",
                        "operation": response.operation,
                        "steps": len(response.steps),
                    },
                )
        if not response.needs_refinement and response != grounded_response:
            response = retain_screen_observation(
                response, request, services, prepared, page=page if used_image else None
            )
            response = sanitize_unused_controls(services, request.phase, response, screen, required)
        # Recoveries return a complete plan; validate the final one, after every recovery.
        if response != validated_response:
            response = prepare_screen_plan(
                request,
                services,
                response,
                prepared.capability,
                set(prepared.active_fields),
            )
        return response


def decide_screen(
    request: ScreenDecisionRequest,
    services: ScreenDecisionServices,
    *,
    defer_plan_errors: bool = False,
) -> ScreenDecisionResult:
    screen = request.screen
    decision_id = uuid4().hex
    with rejection_evidence(
        services.log,
        "screen_decision.decide_screen",
        context={"screen": request.screen, "phase": request.phase, "decision_id": decision_id},
    ):
        original_call = services.call
        prompt_sizes = []
        mapping_plan_error: ScreenDecisionResult | None = None

        def traced_call(
            prompt: str, schema: type[ResponseT], *, page: RenderedPage | None = None
        ) -> ResponseT:
            nonlocal mapping_plan_error
            prompt_sizes.append(len(prompt))
            with model_call_context(
                decision_id=decision_id,
                frame=str(request.page.image_path) if request.page else None,
                phase=request.phase,
            ):
                if schema in {ScreenMatch, ScreenPlan}:
                    mapped = original_call(prompt, schema, page=page)
                    if mapped.mapping is not None:
                        write_decision_artifact(services, "semantic-mapping", mapped.mapping)
                        plan_error = None
                        try:
                            payload = mapped.mapping.local_payload(screen, request.values)
                        except KeyError:
                            if not defer_plan_errors:
                                raise
                            # Keep the current observation; reject only its unbound next action.
                            plan_error = "screen_match_action_target_undeclared"
                            payload = mapped.mapping.model_copy(update={"steps": []}).local_payload(
                                screen, request.values
                            )
                        if screen.get("unresolved_annotations"):
                            services.log.append(
                                "verify",
                                payload={
                                    "check": "unresolved_mapping_annotations_omitted",
                                    "annotations": screen["unresolved_annotations"],
                                },
                            )
                        response = ScreenMatch.model_validate(payload).model_copy(
                            update={"mapping": mapped.mapping}
                        )
                        if plan_error is not None:
                            mapping_plan_error = ScreenDecisionResult(
                                response, None, plan_error=plan_error
                            )
                            raise ValueError(plan_error)
                        return response
                    return mapped
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
        prepared = build_decision_prompt(request, services)
        capability = prepared.capability
        for allow_method in (True, False):
            method_proposal = reported_response = None
            try:
                selection = select_screen_response(
                    request, services, prepared, allow_method=allow_method
                )
                response = reported_response = selection.response
                cache_hit = selection.cache_hit
                method_proposal = selection.method_proposal
                used_image = selection.used_image
                response = validate_or_replan(response, request, services, prepared)
                response = ground_screen_response(
                    response,
                    request,
                    services,
                    selection.prepared,
                    used_image=used_image,
                )
            except ValueError as error:
                if mapping_plan_error is not None:
                    return mapping_plan_error
                if method_proposal is None:
                    if (
                        defer_plan_errors
                        and reported_response is not None
                        and str(error).startswith("screen_match_")
                    ):
                        # Keep observations without caching the rejected next action.
                        return ScreenDecisionResult(reported_response, None, plan_error=str(error))
                    raise
                rejected = services.memory_retrieval.reject_pending(reason=str(error))
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
                # Retry once without the rejected method; retain the caller's education focus.
                request = replace(request, proposal=None)
                prepared = build_decision_prompt(request, services)
            else:
                break
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
        state = vars(prepared).get("state")
        if state is not None:
            services.log.append(
                "verify",
                payload={
                    "check": "match_context_size",
                    "frame": str(request.page.image_path) if request.page else None,
                    "sections_utf8_bytes": {
                        key: len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
                        for key, value in state.items()
                    },
                    "selection_source": "method"
                    if method_proposal is not None
                    else "visual_cache"
                    if cache_hit is not None
                    else "model",
                },
            )
        remember_response = response != method_proposal
        if response.mapping is not None:
            response = response.model_copy(update={"mapping": response.accepted_mapping(screen)})
        visual_match_candidate = (
            (
                cache_hit[0] if cache_hit is not None else prepared.cache_key,
                {
                    **response.model_dump(mode="json"),
                    **(
                        {"mapping": response.mapping.model_dump(mode="json", exclude_unset=True)}
                        if response.mapping
                        else {}
                    ),
                },
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
                "prompt_chars": sum(prompt_sizes),
            },
        )
        if services.memory_retrieval.enabled and remember_response:
            services.memory_retrieval.remember(screen, response, allow_recovery=cache_hit is None)
        return ScreenDecisionResult(
            response=response,
            visual_match_candidate=visual_match_candidate,
            observation_claim=observation_claim,
        )


def compile_match(response: ScreenMatch, observation, schema, *, log: RunLog | None = None):
    from .schemas import DecisionResponse

    prefix = "e" + str(observation.obs_id.int) + "0"
    allowed_refs = {element.ref for element in observation.elements}

    def ref(target):
        if target is None:
            return None
        result = prefix + str(target)
        if result not in allowed_refs:
            raise ValueError("visual_proposal_unknown_marker")
        return result

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
    if schema is DecisionResponse and len(response.steps) <= 1:
        data = decision(response.steps[0] if response.steps else None)
    elif credentials:
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
    # Acknowledging a completed save is navigation, even when the model calls
    # the same button click confirm_save. Preserve the raw response and target.
    normalized_acknowledgement = (
        response.save_result == "ready_for_query"
        and response.operation == "act"
        and len(response.steps) == 1
        and response.steps[0].action == "click"
        and response.steps[0].intent == "confirm_save"
    )
    if normalized_acknowledgement:
        data["decision"]["intent"] = "navigate"
    if response.save_result is not None:
        data = {
            "result": response.save_result,
            "evidence_refs": [ref(i) for i in response.message_targets],
            "repair_fields": response.repair_fields,
            "next_decision": data,
        }

    result = adapt_proposal(data, schema)
    if normalized_acknowledgement and log is not None:
        log.append(
            "verify",
            payload={
                "check": "save_acknowledgement_intent_normalized",
                "obs_id": str(observation.obs_id),
                "save_result": response.save_result,
                "original_intent": "confirm_save",
                "execution_intent": "navigate",
                "action": "click",
                "target": response.steps[0].target,
                "point": response.steps[0].point,
            },
        )
    return result


def compile_screen_action_plan(response: ScreenMatch, observation):
    """Preserve the model's ordered current-screen actions in the shared executor."""
    from .execution import ActionPlan, PlannedAction
    from .schemas import DecisionResponse

    if response.operation != "act" or not response.steps:
        return None
    if _is_login_batch(response.steps) or all(step.action == "fill" for step in response.steps):
        # Retain the existing credential and direct-field verification/transport paths.
        return None
    if len(response.steps) == 1 and response.steps[0].intent != "select_option":
        return None
    actions = tuple(
        PlannedAction(
            compile_match(
                response.model_copy(update={"steps": [step]}), observation, DecisionResponse
            ).to_decision(),
            effect_source=step.source
            if step.intent == "select_option" and index == len(response.steps) - 1
            else None,
        )
        for index, step in enumerate(response.steps)
    )
    return ActionPlan(
        actions,
        "single",
        {"check": "same_screen_action_plan", "actions": len(actions)},
    )
