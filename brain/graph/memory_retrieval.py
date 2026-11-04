"""Retrieve learned procedures and field bindings; bind actions only after context recall."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from brain.cache.methods import (
    LearnedMethod,
    MethodActionGroup,
    MethodCompletion,
    MethodDocument,
    MethodLibrary,
    MethodSlot,
    compile_method_candidate,
    group_input_sources,
    merge_method_contents,
    method_completion_from_match,
    method_from_match,
    screen_similarity_distance,
)
from brain.task_instruction import TaskInstruction
from brain.visual.scene import same_control_region
from brain.vocabulary import SCROLL_DIRECTION_KEYS
from sawon_runtime.storage import RunLog, atomic_write_json

from .screen_mapping import ScreenMapping
from .visual_points import is_wheel_scroll

if TYPE_CHECKING:
    from .screen_decision import ScreenMatch


@dataclass(frozen=True)
class RetrievalContext:
    task: TaskInstruction | None
    task_sha256: str | None
    inputs: Mapping[str, str]
    credentials: Mapping[str, str | None]
    observation_id: str | None


@dataclass
class RetrievalState:
    bindings: dict[str, dict[str, str]] = field(default_factory=dict)
    active_method_id: str | None = None
    used: set[tuple[str, str]] = field(default_factory=set)
    purpose: str = ""
    history: list[dict] = field(default_factory=list)
    pending: dict | None = None
    unverified: dict | None = None
    staged: dict[str, LearnedMethod] = field(default_factory=dict)
    staged_completions: dict[str, MethodCompletion] = field(default_factory=dict)
    blocked_method_ids: set[str] = field(default_factory=set)


def open_memory_library(
    path: Path,
    case_dir: Path,
    *,
    enabled: bool,
    promotion_path: Path | None = None,
    compiled_only: bool = False,
    max_phash_distance: int = 6,
) -> MethodLibrary:
    library = MethodLibrary(
        case_dir / "methods-working.json", enabled=enabled, max_phash_distance=max_phash_distance
    )
    if enabled:
        selected = (
            promotion_path if promotion_path is not None and promotion_path.exists() else path
        )
        source = MethodLibrary(selected, enabled=True)
        methods = source.methods()
        if compiled_only:
            methods = [method for method in methods if method.artifact_kind == "warm_compiled"]
        atomic_write_json(library.path, MethodDocument(methods=methods))
    return library


class MemoryRetrieval:
    """Retrieve reusable plans through explicit task, run-state and library inputs."""

    def __init__(
        self,
        context: RetrievalContext,
        state: RetrievalState,
        library: MethodLibrary,
        *,
        case_dir: Path,
        log: RunLog,
        promotion_path: Path | None = None,
    ) -> None:
        self.task_context, self.state, self.library = context, state, library
        self.case_dir, self.log = case_dir, log
        self.enabled = library.enabled
        self.promotion_path = promotion_path

    @property
    def recovery_required(self) -> bool:
        return self.state.active_method_id in self.state.blocked_method_ids

    def suspend_failed_execution(self, *, reason: str) -> bool:
        """Suspend an actual failed edge, preserving an already selected model recovery.

        The observation can prepare the next replay before the previous action's
        verification fails. That speculative replay must not survive the failure.
        A missing/unchanged effect alone does not enter this failure boundary.
        """
        pending = self.state.pending
        delivered = self.state.unverified
        failed = delivered or pending
        if failed is None:
            return False
        method, group_id = failed["method"], failed["group_id"]
        self.state.active_method_id = method.id
        self.state.blocked_method_ids.add(method.id)
        if delivered is not None:
            # Delivery remains in history, but a failed effect cannot advance
            # the route or produce a terminal receipt without model recovery.
            self.state.used.discard((method.id, group_id))
            delivered["failure_reason"] = reason
        discard_replay = pending is not None and not pending.get("recovery_response", False)
        if delivered is not None and discard_replay:
            self.state.pending = None
        # Before/partial delivery, retain pending until delivered(False) records
        # the interrupted attempt. It must never consume the entire group.
        self.log.append(
            "verify",
            payload={
                "check": "method_execution_suspended",
                "method_id": method.id,
                "group_id": group_id,
                "reason": reason,
                "discarded_replay": discard_replay,
            },
        )
        return discard_replay

    def _matches_current_task(self, method: LearnedMethod) -> bool:
        """Keep learned execution inside one exact user-education contract."""

        task = self.task_context.task
        if task is None or method.task_objective != task.objective:
            return False
        digest = self.task_context.task_sha256
        return digest is not None and method.task_contract_sha256 == digest

    def _ordered_groups(self, method: LearnedMethod) -> list[MethodActionGroup]:
        """Preserve compiled order; organize uncompiled learning records by education."""

        # A compiled trace already owns the route, including repeated visits to
        # the same taught step. Education ordering must not reorganize it.
        if self._has_trace_graph(method):
            return sorted(method.action_groups, key=lambda group: group.trace_index)
        if method.artifact_kind == "warm_compiled":
            return list(method.action_groups)
        task = self.task_context.task
        procedure_order = (
            {step.id: index for index, step in enumerate(task.procedure)} if task else {}
        )
        if not procedure_order:
            return list(method.action_groups)
        delivered_order = {
            item["group_id"]: index
            for index, item in enumerate(self.state.history)
            if item.get("method_id") == method.id
            and item.get("status") == "delivered"
            and item.get("group_id")
        }
        original_order = {group.id: index for index, group in enumerate(method.action_groups)}
        return sorted(
            method.action_groups,
            key=lambda group: (
                procedure_order.get(group.taught_step_id, len(procedure_order)),
                delivered_order.get(group.id, len(self.state.history)),
                original_order[group.id],
            ),
        )

    @staticmethod
    def _has_trace_graph(method: LearnedMethod) -> bool:
        return bool(method.action_groups) and all(
            group.trace_index is not None for group in method.action_groups
        )

    def _delivered_groups(self, method: LearnedMethod) -> list[MethodActionGroup]:
        """Select this run's delivered path before retry deduplication."""
        delivered = list(
            dict.fromkeys(
                item["group_id"]
                for item in self.state.history
                if item.get("method_id") == method.id
                and item.get("status") in {"delivered", "provisional"}
                and item.get("group_id")
            )
        )
        by_id = {group.id: group for group in method.action_groups}
        # A promoted graph is the path verified in this successful run. Keeping
        # every historical variant made several actions bind the same screen,
        # which forced model fallback and could replay an obsolete no-op branch.
        return [by_id[group_id] for group_id in delivered if group_id in by_id]

    @staticmethod
    def _compile_trace_graph(method: LearnedMethod) -> LearnedMethod:
        """Link the surviving groups once, after compilation removes retries."""
        groups = []
        previous = None
        for index, group in enumerate(method.action_groups):
            groups.append(
                group.model_copy(
                    update={
                        "trace_index": index,
                        "predecessor_group_ids": [] if previous is None else [previous],
                    }
                )
            )
            previous = group.id
        return method.model_copy(update={"action_groups": groups})

    def _binding_values(self) -> dict[str, str]:
        """Expose credential source availability without exposing credential values."""
        return {
            **self.task_context.inputs,
            **{
                source: "[CREDENTIAL]"
                for source, value in self.task_context.credentials.items()
                if value
            },
        }

    def _remaining_groups(self, method: LearnedMethod) -> list[MethodActionGroup]:
        """Read route progress from deliveries and explicit recovery in this run."""
        passed = {group_id for method_id, group_id in self.state.used if method_id == method.id}
        passed.update(
            item["replaced_edge"][1]
            for item in self.state.history
            if item.get("replaced_edge") and item["replaced_edge"][0] == method.id
        )
        passed.update(
            group_id
            for item in self.state.history
            if item.get("method_id") == method.id
            for group_id in item.get("resumed_group_ids", [])
        )
        # The next observation is prepared before effect review commits a
        # replacement. Existing review still decides whether it can execute.
        pending = self.state.unverified
        if pending and pending.get("replaced_edge") and pending["replaced_edge"][0] == method.id:
            passed.add(pending["replaced_edge"][1])
        return [group for group in self._ordered_groups(method) if group.id not in passed]

    def _action_offset(self, method_id: str, group_id: str) -> int:
        """Read the confirmed prefix, plus the delivery awaiting the next observation."""
        events = self.state.history
        if self.state.unverified is not None:
            events = [*events, self.state.unverified]
        return max(
            (
                item["action_progress"]["end"]
                for item in events
                if item.get("method_id") == method_id
                and (item.get("action_progress") or {}).get("group_id") == group_id
            ),
            default=0,
        )

    def _matching_route(
        self, screen: dict
    ) -> tuple[int, LearnedMethod, MethodActionGroup | MethodCompletion] | None:
        """Match only the next entry of the selected route, or select a route once."""
        matches = []
        for method in self.library.methods():
            if method.id in self.state.blocked_method_ids or not self._matches_current_task(method):
                continue
            if self.state.active_method_id not in {None, method.id}:
                continue
            remaining = self._remaining_groups(method)
            entry = remaining[0] if remaining else method.completion
            if entry is None:
                continue
            distance = screen_similarity_distance(entry, screen, self.library.max_phash_distance)
            if distance is not None:
                matches.append((distance, method, entry))
        if not matches:
            return None
        nearest = min(distance for distance, _, _ in matches)
        matches = [match for match in matches if match[0] == nearest]
        return matches[0] if len(matches) == 1 else None

    def completion_receipt(self, screen: dict) -> dict | None:
        """Describe this run's exhausted route and its matching terminal frame.

        Route completion is independent of field-value review. A model-supplied
        method ID or a remembered success alone cannot establish this receipt.
        """
        if not self.enabled or self.state.active_method_id is None:
            return None
        match = self._matching_route(screen)
        if match is None:
            return None
        distance, method, terminal = match
        if method.id != self.state.active_method_id or not isinstance(terminal, MethodCompletion):
            return None
        history = [item for item in self.state.history if item.get("method_id") == method.id]
        return {
            "method_id": method.id,
            "task_contract_sha256": method.task_contract_sha256,
            "artifact_kind": method.artifact_kind,
            "revision": method.revision,
            "route_group_ids": [group.id for group in self._ordered_groups(method)],
            "delivered_group_ids": list(
                dict.fromkeys(
                    item["group_id"]
                    for item in history
                    if item.get("group_id") and item.get("status") in {"delivered", "provisional"}
                )
            ),
            "resumed_group_ids": list(
                dict.fromkeys(
                    group_id for item in history for group_id in item.get("resumed_group_ids", [])
                )
            ),
            "screen_path": list(terminal.screen_path),
            "screen_phash": screen.get("screen_phash"),
            "distance": distance,
            "terminal_targets": [item.target.name for item in terminal.targets],
        }

    def _resume_from_model(self, method: LearnedMethod, response: ScreenMatch) -> None:
        """An accepted recovery observation may finish a prefix of the current route."""
        completed = {
            result.step_id for result in response.observed_step_results if result.completed
        }
        remaining = self._remaining_groups(method)
        group_ids = [group.id for group in remaining]
        # A current observation can identify an intermediate arrival within one
        # taught step. This is a route cursor, not a claim of delivered actions.
        resume_index = (
            group_ids.index(response.resume_group_id)
            if response.resume_group_id in group_ids
            else None
        )
        resumed = group_ids[:resume_index] if resume_index is not None else []
        if resume_index is None:
            for group in remaining:
                if group.taught_step_id not in completed:
                    break
                resumed.append(group.id)
        if not resumed:
            return
        event = {
            "method_id": method.id,
            "status": "resumed",
            "resumed_group_ids": resumed,
            "completed_step_ids": sorted(completed),
            "resume_group_id": response.resume_group_id if resume_index is not None else None,
            "observation_id": self.task_context.observation_id,
        }
        self.state.history.append(event)
        self.log.append("verify", payload={"check": "method_route_resumed", **event})

    def _redact_credentials(self, text: str) -> str:
        result = text
        for source, value in sorted(
            self.task_context.credentials.items(), key=lambda item: len(item[1] or ""), reverse=True
        ):
            if value:
                result = result.replace(value, "{" + source + "}")
        return result

    def context(self) -> dict:
        task = self.task_context.task
        return {
            "methods": self.library.catalog(
                None if task is None else task.objective,
                self.task_context.task_sha256 if task is not None else None,
            ),
            "current_purpose": self.state.purpose,
            "active_method_id": self.state.active_method_id,
            "slot_bindings": self.state.bindings,
            "recent_executions": self.state.history[-6:],
            "blocked_method_ids": sorted(self.state.blocked_method_ids),
        }

    def prompt_context(self) -> dict:
        """Expose only reusable groups that can affect the next decisions."""
        task = self.task_context.task
        objective = None if task is None else task.objective
        methods = []
        for method in self.library.methods():
            if self.state.active_method_id not in {None, method.id}:
                continue
            if (
                method.id in self.state.blocked_method_ids
                and method.id != self.state.active_method_id
            ):
                continue
            if objective is not None and not self._matches_current_task(method):
                continue
            remaining = self._remaining_groups(method)
            relevant_slots = {
                action.slot for group in remaining for action in group.actions if action.slot
            }
            groups = []
            for group in remaining:
                sources = group_input_sources(method, group, self.state.bindings.get(method.id, {}))
                groups.append(
                    {
                        "id": group.id,
                        "purpose": group.purpose,
                        "taught_step_id": group.taught_step_id,
                        "trace_index": group.trace_index,
                        "predecessor_group_ids": group.predecessor_group_ids,
                        "context_anchors": group.context_anchors,
                        "screen_path": group.screen_path,
                        "expected_result": group.expected_result,
                        "actions": [
                            {
                                "action": action.action,
                                "target": action.target,
                                "source": sources.get(action.target),
                                "keys": action.keys,
                            }
                            for action in group.actions[self._action_offset(method.id, group.id) :]
                        ],
                    }
                )
            methods.append(
                {
                    "id": method.id,
                    "purpose": method.purpose,
                    "task_objective": method.task_objective,
                    "slots": [
                        slot.model_dump() for slot in method.slots if slot.name in relevant_slots
                    ],
                    "groups": groups,
                }
            )
        return {
            "methods": methods,
            "previous_action_purpose": self.state.purpose,
            "active_method_id": self.state.active_method_id,
            "slot_bindings": {
                method_id: bindings
                for method_id, bindings in self.state.bindings.items()
                if any(method["id"] == method_id and method["groups"] for method in methods)
            },
            "recent_executions": self.state.history[-2:],
        }

    def retrieve_screen_memory(self, screen: dict) -> dict | None:
        """Retrieve matching work knowledge without selecting actions or advancing this run."""
        if not self.enabled:
            return None
        match = self._matching_route(screen)
        if match is None:
            return None
        distance, method, group = match
        bindings = self.state.bindings.get(method.id, {})
        if isinstance(group, MethodCompletion):
            sources = {slot.name: bindings.get(slot.name, slot.source_key) for slot in method.slots}
            field_sources = {
                item.target.name: sources[item.slot]
                for item in group.targets
                if item.slot is not None
            }
            targets = [item.target for item in group.targets]
            group_id, predecessors, steps = None, [], []
        else:
            field_sources = group_input_sources(method, group, bindings)
            targets = [*group.observed_fields, *group.targets]
            group_id, predecessors = group.id, group.predecessor_group_ids
            steps = [
                {
                    **action.model_dump(mode="json", exclude={"slot"}),
                    "source": field_sources.get(action.target),
                }
                for action in group.actions[self._action_offset(method.id, group.id) :]
            ]
        fields = []
        for target in targets:
            if target.bbox is None:
                continue
            if any(
                item["role"] == target.role and same_control_region(item["bbox"], target.bbox)
                for item in fields
            ):
                continue
            fields.append(
                {
                    "name": target.name,
                    "role": target.role,
                    "bbox": list(target.bbox),
                    "source": field_sources.get(target.name),
                }
            )
        if not fields:
            return None
        return {
            "distance": distance,
            "groups": [{"method_id": method.id, "group_id": group_id}],
            "fields": fields,
            "procedure": [
                {
                    "method_id": method.id,
                    "group_id": group_id,
                    "purpose": group.purpose,
                    "taught_step_id": group.taught_step_id,
                    "predecessor_group_ids": predecessors,
                    "steps": steps,
                    "expected_result": group.expected_result,
                }
            ],
        }

    def match_pattern(self, screen: dict) -> ScreenMatch | None:
        from .screen_decision import ScreenMatch

        self.state.pending = None
        task = self.task_context.task
        if not self.enabled or task is None:
            return None
        match = self._matching_route(screen)
        if match is None:
            return None
        _, method, group = match
        if (
            method.id != self.state.active_method_id
            and not task.procedure
            and not self._has_trace_graph(method)
        ):
            return None
        mapping = self.state.bindings.get(method.id) or {
            slot.name: slot.source_key for slot in method.slots
        }
        if isinstance(group, MethodCompletion):
            completion = self.library.bind_completion_with_context(
                method, mapping, screen, self.task_context.inputs
            )
            if completion is None:
                return None
            response = ScreenMatch.model_validate(
                {
                    **completion,
                    "method_id": method.id,
                    "method_bindings": [
                        {"slot": slot, "source": source} for slot, source in mapping.items()
                    ],
                }
            )
            if method.completion.screen_path:
                sources = {
                    slot.name: mapping.get(slot.name, slot.source_key) for slot in method.slots
                }
                fields = {
                    item.target.name: dict(
                        role=item.target.role,
                        bbox=list(item.target.bbox),
                        **({"source": sources[item.slot]} if item.slot is not None else {}),
                    )
                    for item in method.completion.targets
                    if item.target.bbox is not None
                }
                response = self._restore_structure(
                    screen,
                    response,
                    method.completion.screen_path,
                    fields,
                )
            self.state.purpose = response.purpose
            self.state.bindings[method.id] = mapping
            self.log.append(
                "cache_hit",
                payload={
                    "check": "method_completion_bound",
                    "method_id": method.id,
                    "task_contract_sha256": method.task_contract_sha256,
                    "artifact_kind": method.artifact_kind,
                    "revision": method.revision,
                    "purpose": self.state.purpose,
                    "slot_map": mapping,
                    "evidence_fields": [item.field for item in response.evidence],
                },
            )
            return response
        bound = self.library.bind_with_context(
            method, mapping, screen, self._binding_values(), group_id=group.id
        )
        if bound is None:
            return None
        response_data = dict(bound.response)
        offset = self._action_offset(method.id, group.id)
        response_data["steps"] = response_data["steps"][offset:]
        response = ScreenMatch.model_validate(
            {
                **response_data,
                "procedure_step": "task-action",
                "purpose": response_data.get("purpose") or method.purpose[:160],
                "method_id": method.id,
                "taught_step_id": response_data.get("taught_step_id"),
                "method_bindings": [
                    {"slot": slot, "source": source} for slot, source in mapping.items()
                ],
            }
        )
        if group.screen_path:
            field_sources = group_input_sources(method, group, mapping)
            fields = {
                target.name: {
                    "role": target.role,
                    "bbox": list(target.bbox),
                    "source": field_sources.get(target.name),
                }
                for target in [*group.observed_fields, *group.targets]
                if target.bbox is not None
            }
            response = self._restore_structure(screen, response, group.screen_path, fields)
        self.state.purpose = response.purpose
        self.state.bindings[method.id] = mapping
        self.state.active_method_id = method.id
        self.state.pending = {
            "method": method,
            "group_id": bound.group_id,
            "origin": "reuse",
        }
        if offset:
            # Stage the actual suffix, so subsequent promotion cannot restore
            # already performed inputs into this delivery's action group.
            self.remember(screen, response, allow_recovery=False)
        self.log.append(
            "cache_hit",
            payload={
                "check": "method_bound",
                "method_id": method.id,
                "task_contract_sha256": method.task_contract_sha256,
                "artifact_kind": method.artifact_kind,
                "revision": method.revision,
                "source_transitions": method.source_transitions,
                "group_id": bound.group_id,
                "purpose": self.state.purpose,
                "slot_map": mapping,
                "steps": [step.model_dump(mode="json") for step in response.steps],
            },
        )
        return response

    def _restore_structure(self, screen, response, screen_path, fields):
        """Recall field bindings and actions without claiming values on the current frame."""
        names = {int(ref): item["name"] for ref, item in screen.get("selected_fields", {}).items()}
        steps = []
        directions = {key: direction for direction, key in SCROLL_DIRECTION_KEYS.items()}
        for step in response.steps:
            data = step.model_dump(exclude_none=True)
            data["target"] = names[step.target]
            if step.action == "key" and step.keys in directions:
                data.update(action="scroll", direction=directions[data.pop("keys")])
            steps.append(data)
        mapping = ScreenMapping(
            **response.model_dump(
                include=set(ScreenMapping.model_fields)
                - {"steps", "evidence", "completion_evidence", "message_targets", "grid", "row"}
            ),
            screen_path=screen_path,
            fields=fields,
            steps=steps,
            message_targets=[names[target] for target in response.message_targets],
            completion_evidence=[
                dict(condition_index=item.condition_index, target=names[item.target])
                for item in response.completion_evidence
            ],
        )
        screen["screen_path"] = list(screen_path)
        screen["memory_fields"] = [
            dict(name=name, **field.model_dump(exclude_unset=True))
            for name, field in mapping.fields.items()
        ]
        return response.model_copy(
            update={"mapping": mapping, "method_bindings": response.method_bindings}
        )

    def reject_pending(self, *, reason: str) -> dict | None:
        """Reject the method edge that failed before it reached the relay.

        A grounding or contract failure belongs to the newly proposed edge, not
        to the previously delivered edge that is still awaiting effect review.
        Suspend replay until the model supplies a recovery response. Keep the
        route position so a matching descendant cannot jump over the rejected edge.
        """

        pending, self.state.pending = self.state.pending, None
        if pending is None:
            return None
        method, group_id = pending["method"], pending["group_id"]
        self.state.blocked_method_ids.add(method.id)
        event = {
            "method_id": method.id,
            "task_contract_sha256": method.task_contract_sha256,
            "artifact_kind": method.artifact_kind,
            "revision": method.revision,
            "group_id": group_id,
            "purpose": self.state.purpose or method.purpose,
            "origin": pending["origin"],
            "taught_step_id": pending.get("taught_step_id")
            or next(
                (group.taught_step_id for group in method.action_groups if group.id == group_id),
                None,
            ),
            "status": "rejected_before_delivery",
            "reason": reason[:160],
            "observation_id": self.task_context.observation_id,
        }
        self.state.history.append(event)
        self.log.append("verify", payload={"check": "method_execution", **event})
        atomic_write_json(self.case_dir / "method-progress.json", self.context())
        return event

    def _replacement_progress(
        self,
        method: LearnedMethod,
        mapping: dict[str, str],
        screen: dict,
        response: ScreenMatch,
    ) -> dict | None:
        """Attribute the performed prefix without requiring the original batch boundary."""
        remaining = self._remaining_groups(method)
        if not remaining or not response.steps:
            return None
        group = remaining[0]
        # Attribute the model's recovery action, not a saved coordinate replay.
        # Scrolling may change the image while leaving this control in place.
        if tuple(screen.get("image_size", ())) != group.image_size:
            return None
        targets = {target.name: target for target in group.targets}
        sources = {slot.name: mapping.get(slot.name, slot.source_key) for slot in method.slots}
        start = self._action_offset(method.id, group.id)
        end = start
        for actual, expected in zip(response.steps, group.actions[start:], strict=False):
            if (actual.action, actual.source, actual.keys) != (
                expected.action,
                sources.get(expected.slot),
                expected.keys,
            ):
                break
            if is_wheel_scroll(actual.action, actual.keys):
                end += 1
                continue
            target = targets[expected.target]
            if actual.point is not None and tuple(actual.point) == (expected.point or target.point):
                end += 1
                continue
            # Recovery may allocate different marker IDs. Compare the actual
            # control region, never a historical frame-local ID or explanation.
            if target.bbox is None or actual.point is None:
                break
            x, y, width, height = target.bbox
            if not (x <= actual.point[0] <= x + width and y <= actual.point[1] <= y + height):
                break
            end += 1
        return {"group_id": group.id, "start": start, "end": end} if end > start else None

    def remember(self, screen: dict, response: ScreenMatch, *, allow_recovery: bool = True) -> None:
        self.state.pending = None
        if not self.enabled:
            return
        task = self.task_context.task
        source_response = response.mapping if response.mapping is not None else response
        redacted = {}
        for key in ("purpose", "expected_result"):
            original = getattr(source_response, key)
            value = self._redact_credentials(original)
            if value != original:
                redacted[key] = value
        results = source_response.observed_step_results
        result_texts = [self._redact_credentials(result.observed_result) for result in results]
        if any(
            value != result.observed_result
            for result, value in zip(results, result_texts, strict=True)
        ):
            redacted["observed_step_results"] = [
                {**result.model_dump(mode="json"), "observed_result": value}
                for result, value in zip(results, result_texts, strict=True)
            ]
        # Keep validated mappings intact unless redaction changes their contents.
        # Changed dictionaries still pass through the compiler's mapping validation.
        sanitized = (
            {
                **source_response.model_dump(
                    mode="json", exclude_unset=response.mapping is not None
                ),
                **redacted,
            }
            if redacted or response.mapping is None
            else source_response
        )
        self.state.purpose = self._redact_credentials(response.purpose[:160])
        known = {method.id: method for method in self.library.methods()}
        previous = known.get(response.method_id or self.state.active_method_id)
        if previous is None and self.task_context.task is not None:
            matches = [method for method in known.values() if self._matches_current_task(method)]
            previous = matches[0] if len(matches) == 1 else None
        replaced_edge = None
        action_progress = None
        if previous is not None and task is not None and not self._matches_current_task(previous):
            previous = None
        mapping = {item.slot: item.source for item in response.method_bindings}
        if previous is not None:
            mapping = (
                mapping
                or self.state.bindings.get(previous.id)
                or {slot.name: slot.source_key for slot in previous.slots}
            )
            if any(source not in self._binding_values() for source in mapping.values()):
                self.state.blocked_method_ids.add(previous.id)
                return
            self.state.bindings[previous.id] = mapping
            self.state.active_method_id = previous.id
            if allow_recovery:
                self._resume_from_model(previous, response)
                self.state.blocked_method_ids.discard(previous.id)
            action_progress = self._replacement_progress(previous, mapping, screen, response)
            if action_progress is not None:
                original = next(
                    group
                    for group in previous.action_groups
                    if group.id == action_progress["group_id"]
                )
                if action_progress["end"] == len(original.actions):
                    replaced_edge = (previous.id, original.id)
        if response.operation == "complete":
            available = dict(known)
            available.update(self.state.staged)
            if previous is None and task is not None:
                matches = [
                    method for method in available.values() if self._matches_current_task(method)
                ]
                previous = matches[0] if len(matches) == 1 else None
            if previous is None:
                return
            learned = method_completion_from_match(
                screen, sanitized, previous, self._binding_values()
            )
            if learned is not None and learned.completion is not None:
                self.state.staged_completions[learned.id] = learned.completion
            return
        if not response.steps:
            return
        purpose = self.state.purpose or response.expected_result
        if previous is None:
            identity = (
                task.objective + ":" + (self.task_context.task_sha256 or "") if task else purpose
            )
            method_id = "method-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
            slots = [
                MethodSlot(name=source, description=source, source_key=source)
                for source in dict.fromkeys(step.source for step in response.steps if step.source)
            ]
        else:
            method_id = previous.id
            slots = [
                slot.model_copy(update={"source_key": mapping.get(slot.name, slot.source_key)})
                for slot in previous.slots
            ]
            covered = {slot.source_key for slot in slots}
            slots.extend(
                MethodSlot(name=source, description=source, source_key=source)
                for source in dict.fromkeys(step.source for step in response.steps if step.source)
                if source not in covered
            )
        method = method_from_match(
            screen,
            sanitized,
            method_id=method_id,
            purpose=previous.purpose if previous else purpose,
            slots=slots,
            task_objective=task.objective if task else "",
            task_contract_sha256=(self.task_context.task_sha256 or "") if task else "",
            values=self._binding_values(),
        )
        if method is not None:
            if previous is not None:
                method = method.model_copy(update={"artifact_kind": previous.artifact_kind})
            self.state.pending = {
                "method": method,
                "group_id": method.action_groups[0].id,
                "origin": "adaptation" if previous else "learned",
                "recovery_response": allow_recovery,
                "taught_step_id": response.taught_step_id,
                "replaced_edge": replaced_edge,
                "action_progress": action_progress,
            }

    def _stage(self, method) -> None:
        previous = self.state.staged.get(method.id)
        self.state.staged[method.id] = (
            previous.model_copy(update=merge_method_contents(previous, method))
            if previous is not None
            else method
        )

    def delivered(self, *, complete: bool) -> None:
        pending, self.state.pending = self.state.pending, None
        self.state.unverified = None
        if pending is None:
            return
        method, group_id = pending["method"], pending["group_id"]
        status = "delivered" if complete else "interrupted"
        event = {
            "method_id": method.id,
            "group_id": group_id,
            "purpose": self.state.purpose or method.purpose,
            "origin": pending["origin"],
            "taught_step_id": pending.get("taught_step_id")
            or next(
                (group.taught_step_id for group in method.action_groups if group.id == group_id),
                None,
            ),
            "status": status,
            "observation_id": self.task_context.observation_id,
        }
        if status == "delivered":
            self.state.used.add((method.id, group_id))
            self.state.active_method_id = method.id
            if pending["origin"] != "reuse":
                # A prior effect can fail after this model recovery was selected.
                if pending.get("recovery_response", False):
                    self.state.blocked_method_ids.discard(method.id)
                transitions = (
                    [event["observation_id"]] if event["observation_id"] is not None else []
                )
                method = method.model_copy(update={"source_transitions": transitions})
                self._stage(method)
        self.state.history.append(event)
        if status == "delivered":
            self.state.unverified = {
                "method": method,
                "method_id": method.id,
                "group_id": group_id,
                "origin": pending["origin"],
                "history_index": len(self.state.history) - 1,
                "replaced_edge": pending.get("replaced_edge"),
                "action_progress": pending.get("action_progress"),
            }
        self.log.append("verify", payload={"check": "method_execution", **event})
        atomic_write_json(self.case_dir / "method-progress.json", self.context())

    def verify_delivery(self, *, success: bool | None) -> None:
        """Record effects separately from acknowledged, possibly split input delivery."""

        delivered, self.state.unverified = self.state.unverified, None
        if delivered is None:
            return
        method = delivered["method"]
        group_id = delivered["group_id"]
        failure_reason = delivered.get("failure_reason")
        if failure_reason is not None:
            success = False
        group = next((item for item in method.action_groups if item.id == group_id), None)
        input_delivery = bool(
            group is not None and group.actions and all(a.action == "fill" for a in group.actions)
        )
        if success is True or (success is None and input_delivery):
            progress = delivered.get("action_progress")
            # The next plan may have been prepared before the previous effect
            # review. If that prefix failed, its suffix cannot complete the gap.
            connected = progress is None or progress["start"] <= self._action_offset(
                method.id, progress["group_id"]
            )
            if progress is not None and connected:
                self.state.history[delivered["history_index"]]["action_progress"] = delivered[
                    "action_progress"
                ]
                self.log.append(
                    "verify",
                    payload={
                        "check": "method_actions_matched",
                        "method_id": method.id,
                        "replacement_group_id": group_id,
                        **delivered["action_progress"],
                    },
                )
            if connected and delivered.get("replaced_edge") is not None:
                self.state.history[delivered["history_index"]]["replaced_edge"] = delivered[
                    "replaced_edge"
                ]
                self.log.append(
                    "verify",
                    payload={
                        "check": "method_edge_replaced",
                        "method_id": delivered["replaced_edge"][0],
                        "group_id": delivered["replaced_edge"][1],
                        "replacement_group_id": group_id,
                    },
                )
        if success is not False:
            if success is None:
                self.state.history[delivered["history_index"]]["status"] = "provisional"
            self.log.append(
                "verify",
                payload={
                    "check": "method_effect",
                    "method_id": method.id,
                    "group_id": group_id,
                    "success": success,
                    **(
                        {
                            "reason": "input_review_deferred"
                            if input_delivery
                            else "observation_unavailable"
                        }
                        if success is None
                        else {}
                    ),
                },
            )
            return
        history_index = delivered["history_index"]
        if (
            failure_reason is None
            and group is not None
            and any(action.action == "fill" for action in group.actions)
        ):
            # Pixel rereads can miss a just-delivered value. Keep the batch
            # provisional; whole-task success is the later business proof that
            # decides whether it is promoted.
            if history_index < len(self.state.history):
                self.state.history[history_index]["status"] = "provisional"
            self.log.append(
                "verify",
                payload={
                    "check": "method_effect",
                    "method_id": method.id,
                    "group_id": group_id,
                    "success": False,
                    "provisional": True,
                },
            )
            atomic_write_json(self.case_dir / "method-progress.json", self.context())
            return
        # A failed learned candidate is discarded. A failed replay edge remains
        # consumed for this run so the graph can try its next verified branch
        # instead of selecting the same no-op forever.
        if delivered["origin"] != "reuse" or failure_reason is not None:
            self.state.used.discard((method.id, group_id))
        if history_index < len(self.state.history):
            self.state.history[history_index]["status"] = (
                "failed" if failure_reason is not None else "unproductive"
            )
        if delivered["origin"] != "reuse":
            candidate = self.state.staged.get(method.id)
            if candidate is not None:
                groups = [item for item in candidate.action_groups if item.id != group_id]
                if groups:
                    self.state.staged[method.id] = candidate.model_copy(
                        update={"action_groups": groups}
                    )
                else:
                    self.state.staged.pop(method.id, None)
        self.log.append(
            "verify",
            payload={
                "check": "method_effect",
                "method_id": method.id,
                "group_id": group_id,
                "success": False,
                **({"reason": failure_reason} if failure_reason is not None else {}),
            },
        )
        atomic_write_json(self.case_dir / "method-progress.json", self.context())

    def finish(self, *, success: bool) -> int:
        candidates = dict(self.state.staged)
        known = {method.id: method for method in self.library.methods()}
        for method_id, completion in self.state.staged_completions.items():
            method = candidates.get(method_id) or known.get(method_id)
            if method is not None:
                candidates[method_id] = method.model_copy(update={"completion": completion})
        for method_id, candidate in list(candidates.items()):
            previous = known.get(method_id)
            if previous is not None:
                candidate = candidate.model_copy(
                    update={
                        **merge_method_contents(previous, candidate),
                        "artifact_kind": previous.artifact_kind,
                    }
                )
            candidates[method_id] = candidate.model_copy(
                update={"action_groups": self._ordered_groups(candidate)}
            )
        methods = []
        for method in candidates.values():
            delivered = self._delivered_groups(method)
            compiled = compile_method_candidate(
                method.model_copy(update={"action_groups": delivered}) if delivered else method,
                {
                    **self.task_context.inputs,
                    **{
                        source: value
                        for source, value in self.task_context.credentials.items()
                        if value
                    },
                },
            )
            if delivered:
                compiled = self._compile_trace_graph(compiled)
            methods.append(compiled)
        promoted = 0
        promoted_methods = []
        installed_methods = []
        if success:
            for method in methods:
                promoted_methods.append(self.library.upsert(method, replace_action_groups=True))
                promoted += 1
            if promoted_methods:
                installed_methods = promoted_methods
                if self.promotion_path is not None:
                    destination = MethodLibrary(self.promotion_path, enabled=True)
                    installed_methods = [
                        destination.upsert(method, replace_action_groups=True)
                        for method in promoted_methods
                    ]
                atomic_write_json(
                    self.case_dir / "method-candidate.json",
                    # Export the full merged method. Memory reuse often learns only
                    # the suffix that required model fallback; that suffix alone
                    # is not a replayable business procedure.
                    MethodDocument(methods=installed_methods),
                )
        atomic_write_json(
            self.case_dir / "method-learning-result.json",
            {
                "status": "candidate" if success and methods else "incomplete",
                "business_success": success,
                "task_contract_sha256": self.task_context.task_sha256,
                "method_count": len(methods),
                "methods": [
                    {
                        "id": method.id,
                        "task_contract_sha256": method.task_contract_sha256,
                        "artifact_kind": method.artifact_kind,
                        "revision": method.revision,
                        "source_transitions": method.source_transitions,
                    }
                    for method in installed_methods or promoted_methods or methods
                ],
                "action_group_count": sum(len(item.action_groups) for item in promoted_methods),
                "completion_count": sum(item.completion is not None for item in promoted_methods),
                "promoted": promoted,
                "installed": len(installed_methods) if self.promotion_path is not None else 0,
                "installed_revisions": {method.id: method.revision for method in installed_methods}
                if self.promotion_path is not None
                else {},
            },
        )
        return promoted
