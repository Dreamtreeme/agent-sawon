"""Compile a successful run, then replay its current-screen-bound execution graph."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from brain.cache.methods import (
    LearnedMethod,
    MethodCompletion,
    MethodDocument,
    MethodLibrary,
    MethodSlot,
    compile_method_candidate,
    method_completion_from_match,
    method_from_match,
)
from brain.task_instruction import TaskInstruction
from sawon_runtime.storage import RunLog, atomic_write_json

if TYPE_CHECKING:
    from .matching import ScreenMatch


@dataclass(frozen=True)
class MethodContext:
    task: TaskInstruction | None
    task_sha256: str | None
    inputs: Mapping[str, str]
    credentials: Mapping[str, str | None]
    observation_id: str | None


@dataclass
class MethodState:
    bindings: dict[str, dict[str, str]] = field(default_factory=dict)
    active_method_id: str | None = None
    used: set[tuple[str, str]] = field(default_factory=set)
    purpose: str = ""
    history: list[dict] = field(default_factory=list)
    pending: dict | None = None
    unverified: dict | None = None
    staged: dict[str, MethodDocument] = field(default_factory=dict)
    staged_completions: dict[str, MethodCompletion] = field(default_factory=dict)
    blocked_method_ids: set[str] = field(default_factory=set)


def open_method_library(
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


class MethodRuntime:
    """A short-lived view of explicit task data, run state and a persistent library."""

    def __init__(
        self,
        context: MethodContext,
        state: MethodState,
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

    def _matches_current_task(self, method: LearnedMethod) -> bool:
        """Keep learned execution inside one exact user-education contract."""

        task = self.task_context.task
        if task is None or method.task_objective != task.objective:
            return False
        digest = self.task_context.task_sha256
        return digest is not None and method.task_contract_sha256 == digest

    def _ordered_groups(self, method: LearnedMethod) -> list:
        """Order a reusable method by the taught procedure and successful trace.

        A screen discovered late can belong at the beginning of the business
        procedure (login is the common example).  File insertion order therefore
        cannot be the execution contract.
        """

        task = self.task_context.task
        procedure_order = (
            {step.id: index for index, step in enumerate(task.procedure)} if task else {}
        )
        if not procedure_order:
            if self._has_trace_graph(method):
                return sorted(method.action_groups, key=lambda group: group.trace_index)
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

    def _compile_trace_graph(self, method: LearnedMethod) -> LearnedMethod:
        """Turn the delivered successful path into a reusable execution graph."""

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
        order = [group_id for group_id in delivered if group_id in by_id]
        # A promoted graph is the path verified in this successful run. Keeping
        # every historical variant made several actions bind the same screen,
        # which forced model fallback and could replay an obsolete no-op branch.
        if not order:
            return method
        groups = []
        previous = None
        for index, group_id in enumerate(order):
            group = by_id[group_id]
            groups.append(
                group.model_copy(
                    update={
                        "trace_index": index,
                        "predecessor_group_ids": [] if previous is None else [previous],
                    }
                )
            )
            previous = group_id
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
            if method.id in self.state.blocked_method_ids:
                continue
            if objective is not None and not self._matches_current_task(method):
                continue
            remaining = [
                group
                for group in self._ordered_groups(method)
                if (method.id, group.id) not in self.state.used
            ][:2]
            relevant_slots = {
                action.slot for group in remaining for action in group.actions if action.slot
            }
            methods.append(
                {
                    "id": method.id,
                    "purpose": method.purpose,
                    "task_objective": method.task_objective,
                    "slots": [
                        slot.model_dump() for slot in method.slots if slot.name in relevant_slots
                    ],
                    "groups": [
                        {
                            "id": group.id,
                            "purpose": group.purpose,
                            "taught_step_id": group.taught_step_id,
                            "trace_index": group.trace_index,
                            "predecessor_group_ids": group.predecessor_group_ids,
                            "context_anchors": group.context_anchors,
                        }
                        for group in remaining
                    ],
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

    def apply(self, screen: dict) -> ScreenMatch | None:
        from .matching import ScreenMatch

        self.state.pending = None
        pending_predecessor = (
            self.state.unverified.get("replaced_edge") if self.state.unverified else None
        )
        task = self.task_context.task
        if not self.enabled or task is None:
            return None
        candidates = []
        completion_candidates = []
        for method in self.library.methods():
            if method.id in self.state.blocked_method_ids:
                continue
            taught_replay = bool(task.procedure) and self._matches_current_task(method)
            trace_replay = self._has_trace_graph(method) and self._matches_current_task(method)
            auto_replay = taught_replay or trace_replay
            if method.id != self.state.active_method_id and not auto_replay:
                continue
            mapping = self.state.bindings.get(method.id)
            if mapping is None:
                if not self._matches_current_task(method):
                    continue
                mapping = {slot.name: slot.source_key for slot in method.slots}
            remaining = [
                group
                for group in self._ordered_groups(method)
                if (method.id, group.id) not in self.state.used
            ]
            # The current screen chooses a taught branch. Earlier versions tried
            # only the first unused group, so an already advanced screen could
            # never reuse a later successful action and fell back to the model.
            if trace_replay:
                groups = [
                    group
                    for group in remaining
                    if all(
                        (method.id, predecessor) in self.state.used
                        or (method.id, predecessor) == pending_predecessor
                        for predecessor in group.predecessor_group_ids
                    )
                ]
            elif taught_replay:
                groups = remaining
            else:
                groups = remaining
            for group_index, group in enumerate(groups):
                if (method.id, group.id) in self.state.used:
                    continue
                bound = self.library.bind_with_context(
                    method.id, mapping, screen, self._binding_values(), group_id=group.id
                )
                if bound is not None:
                    pruned_group_ids = (
                        [item.id for item in groups[:group_index]] if taught_replay else []
                    )
                    candidates.append((method, mapping, bound, pruned_group_ids))
            if not remaining and method.completion is not None:
                completion = self.library.bind_completion_with_context(
                    method.id, mapping, screen, self.task_context.inputs
                )
                if completion is not None:
                    completion_candidates.append((method, mapping, completion))
        if not candidates and len(completion_candidates) == 1:
            method, mapping, completion = completion_candidates[0]
            response = ScreenMatch.model_validate(
                {
                    **completion,
                    "method_id": method.id,
                    "method_bindings": [
                        {"slot": slot, "source": source} for slot, source in mapping.items()
                    ],
                }
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
        if len(candidates) != 1:
            return None
        method, mapping, bound, pruned_group_ids = candidates[0]
        proof_controls, proof_evidence = self.library.bind_visible_value_evidence(
            method.id,
            mapping,
            screen,
            dict(self.task_context.inputs),
        )
        response_data = dict(bound.response)
        if proof_evidence:
            controls = {
                item["id"]: item for item in [*response_data.get("controls", []), *proof_controls]
            }
            response_data.update(
                {
                    "controls": list(controls.values()),
                    "evidence": proof_evidence,
                }
            )
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
        self.state.purpose = response.purpose
        self.state.bindings[method.id] = mapping
        self.state.pending = {
            "method": method,
            "group_id": bound.group_id,
            "origin": "reuse",
            "pruned_group_ids": pruned_group_ids,
            "response": response,
        }
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

    def reject_pending(self, *, reason: str) -> dict | None:
        """Reject the method edge that failed before it reached the relay.

        A grounding or contract failure belongs to the newly proposed edge, not
        to the previously delivered edge that is still awaiting effect review.
        Suspend the whole method for this run so a descendant cannot jump over
        the rejected edge; screen matching then falls through to visual cache or
        the model on the same observation.
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

    def remember(self, screen: dict, response: ScreenMatch) -> None:
        bound_pending = self.state.pending
        self.state.pending = None
        if not self.enabled:
            return
        task = self.task_context.task
        self.state.purpose = self._redact_credentials(response.purpose[:160])
        known = {method.id: method for method in self.library.methods()}
        previous = known.get(response.method_id)
        replaced_edge = None
        if bound_pending is not None and bound_pending.get("response") is not None:
            bound_response = bound_pending["response"]

            # Only the same concrete actions on this frame replace a bound edge.
            # Semantic labels, explanations and transient marker IDs may differ.
            def same_action(actual, expected):
                if (actual.action, actual.source, actual.keys) != (
                    expected.action,
                    expected.source,
                    expected.keys,
                ):
                    return False
                if actual.point == expected.point and actual.point is not None:
                    return True
                region = screen.get("selected_fields", {}).get(str(expected.target), {}).get("bbox")
                if region is not None and actual.point is not None:
                    x, y, width, height = region
                    return x <= actual.point[0] <= x + width and y <= actual.point[1] <= y + height
                return (
                    actual.point is None
                    and expected.point is None
                    and actual.target == expected.target
                )

            if (
                response.steps
                and len(response.steps) == len(bound_response.steps)
                and all(
                    same_action(actual, expected)
                    for actual, expected in zip(response.steps, bound_response.steps, strict=True)
                )
            ):
                previous = bound_pending["method"]
                replaced_edge = (previous.id, bound_pending["group_id"])
        if previous is not None and task is not None and not self._matches_current_task(previous):
            previous = None
        if response.operation == "complete":
            available = dict(known)
            available.update(
                (method_id, document.methods[0])
                for method_id, document in self.state.staged.items()
            )
            if previous is None and task is not None:
                matches = [
                    method for method in available.values() if self._matches_current_task(method)
                ]
                previous = matches[0] if len(matches) == 1 else None
            if previous is None:
                return
            learned = method_completion_from_match(
                screen, response.model_dump(mode="json"), previous
            )
            if learned is not None and learned.completion is not None:
                self.state.staged_completions[learned.id] = learned.completion
            return
        self.state.active_method_id = None
        mapping = {item.slot: item.source for item in response.method_bindings}
        if previous is not None:
            mapping = (
                mapping
                or self.state.bindings.get(previous.id)
                or {slot.name: slot.source_key for slot in previous.slots}
            )
            if any(source not in self._binding_values() for source in mapping.values()):
                return
            self.state.bindings[previous.id] = mapping
            self.state.active_method_id = previous.id
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
        sanitized = response.model_dump(mode="json")
        sanitized["purpose"] = self._redact_credentials(sanitized["purpose"])
        sanitized["expected_result"] = self._redact_credentials(sanitized["expected_result"])
        method = method_from_match(
            screen,
            sanitized,
            method_id=method_id,
            purpose=previous.purpose if previous else purpose,
            slots=slots,
            task_objective=task.objective if task else "",
            task_contract_sha256=(self.task_context.task_sha256 or "") if task else "",
        )
        if method is not None:
            if previous is not None:
                method = method.model_copy(update={"artifact_kind": previous.artifact_kind})
            self.state.pending = {
                "method": method,
                "group_id": method.action_groups[0].id,
                "origin": "adaptation" if previous else "learned",
                "taught_step_id": response.taught_step_id,
                "replaced_edge": replaced_edge,
            }

    def _stage(self, method) -> None:
        document = self.state.staged.get(method.id)
        if document is None:
            self.state.staged[method.id] = MethodDocument(methods=[method])
            return
        previous = document.methods[0]
        groups = {item.id: item for item in previous.action_groups}
        groups.update({item.id: item for item in method.action_groups})
        slots = {item.name: item for item in previous.slots}
        slots.update({item.name: item for item in method.slots})
        merged = previous.model_copy(
            update={
                "action_groups": list(groups.values()),
                "slots": list(slots.values()),
                "completion": method.completion or previous.completion,
            }
        )
        self.state.staged[method.id] = MethodDocument(methods=[merged])

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
            for pruned_group_id in pending.get("pruned_group_ids", []):
                self.state.used.add((method.id, pruned_group_id))
            self.state.used.add((method.id, group_id))
            self.state.active_method_id = None
            if pending["origin"] != "reuse":
                transitions = (
                    [event["observation_id"]] if event["observation_id"] is not None else []
                )
                method = method.model_copy(update={"source_transitions": transitions})
                self._stage(method)
        self.state.history.append(event)
        if status == "delivered":
            self.state.unverified = {
                "method": method,
                "group_id": group_id,
                "origin": pending["origin"],
                "pruned_group_ids": pending.get("pruned_group_ids", []),
                "history_index": len(self.state.history) - 1,
                "replaced_edge": pending.get("replaced_edge"),
            }
        self.log.append("verify", payload={"check": "method_execution", **event})
        atomic_write_json(self.case_dir / "method-progress.json", self.context())

    def verify_delivery(self, *, success: bool | None) -> None:
        """Keep only a delivered method edge that produced observable progress."""

        delivered, self.state.unverified = self.state.unverified, None
        if delivered is None:
            return
        method = delivered["method"]
        group_id = delivered["group_id"]
        if success is None:
            self.state.history[delivered["history_index"]]["status"] = "provisional"
            self.log.append(
                "verify",
                payload={
                    "check": "method_effect",
                    "method_id": method.id,
                    "group_id": group_id,
                    "success": None,
                    "reason": "observation_unavailable",
                },
            )
            return
        if success:
            if delivered.get("replaced_edge") is not None:
                self.state.used.add(delivered["replaced_edge"])
                self.log.append(
                    "verify",
                    payload={
                        "check": "method_edge_replaced",
                        "method_id": delivered["replaced_edge"][0],
                        "group_id": delivered["replaced_edge"][1],
                        "replacement_group_id": group_id,
                    },
                )
            self.log.append(
                "verify",
                payload={
                    "check": "method_effect",
                    "method_id": method.id,
                    "group_id": group_id,
                    "success": True,
                },
            )
            return
        history_index = delivered["history_index"]
        group = next(
            (item for item in method.action_groups if item.id == group_id),
            None,
        )
        if group is not None and any(action.action == "fill" for action in group.actions):
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
        if delivered["origin"] != "reuse":
            self.state.used.discard((method.id, group_id))
            for pruned_group_id in delivered["pruned_group_ids"]:
                self.state.used.discard((method.id, pruned_group_id))
        if history_index < len(self.state.history):
            self.state.history[history_index]["status"] = "unproductive"
        if delivered["origin"] != "reuse":
            document = self.state.staged.get(method.id)
            if document is not None:
                candidate = document.methods[0]
                groups = [item for item in candidate.action_groups if item.id != group_id]
                if groups:
                    self.state.staged[method.id] = MethodDocument(
                        methods=[candidate.model_copy(update={"action_groups": groups})]
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
            },
        )
        atomic_write_json(self.case_dir / "method-progress.json", self.context())

    def finish(self, *, success: bool) -> int:
        candidates = {
            method_id: document.methods[0] for method_id, document in self.state.staged.items()
        }
        known = {method.id: method for method in self.library.methods()}
        for method_id, completion in self.state.staged_completions.items():
            method = candidates.get(method_id) or known.get(method_id)
            if method is not None:
                candidates[method_id] = method.model_copy(update={"completion": completion})
        for method_id, candidate in list(candidates.items()):
            previous = known.get(method_id)
            if previous is not None:
                groups = {group.id: group for group in previous.action_groups}
                groups.update({group.id: group for group in candidate.action_groups})
                slots = {slot.name: slot for slot in previous.slots}
                slots.update({slot.name: slot for slot in candidate.slots})
                candidate = candidate.model_copy(
                    update={
                        "artifact_kind": previous.artifact_kind,
                        "action_groups": list(groups.values()),
                        "slots": list(slots.values()),
                        "completion": candidate.completion or previous.completion,
                    }
                )
            candidates[method_id] = candidate.model_copy(
                update={"action_groups": self._ordered_groups(candidate)}
            )
        methods = []
        for method in candidates.values():
            compiled = compile_method_candidate(
                self._compile_trace_graph(method),
                {
                    **self.task_context.inputs,
                    **{
                        source: value
                        for source, value in self.task_context.credentials.items()
                        if value
                    },
                },
            )
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
                    # Export the full merged method. A warm run often learns only
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
