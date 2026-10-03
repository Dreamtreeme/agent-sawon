"""Compile a successful taught run, then replay its screen-bound action groups."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from brain.cache.methods import (
    MethodCompletion,
    MethodDocument,
    MethodLibrary,
    MethodSlot,
    compile_method_candidate,
    method_completion_from_match,
    method_from_match,
)
from brain.runtime.storage import atomic_write_json

if TYPE_CHECKING:
    from .engine import Brain
    from .matching import ScreenMatch


class MethodRuntime:
    def __init__(self, brain: Brain, path: Path, *, enabled: bool):
        self.brain = brain
        self.enabled = enabled
        self.library = MethodLibrary(brain.case_dir / "methods-working.json", enabled=enabled)
        if enabled:
            source = MethodLibrary(path, enabled=True)
            atomic_write_json(self.library.path, MethodDocument(methods=source.methods()))
        self.bindings: dict[str, dict[str, str]] = {}
        self.active_method_id: str | None = None
        self.used: set[tuple[str, str]] = set()
        self.purpose = ""
        self.history: list[dict] = []
        self.pending: dict | None = None
        self.staged: dict[str, MethodDocument] = {}
        self.staged_completions: dict[str, MethodCompletion] = {}

    def procedure_context(self) -> dict:
        task = self.brain.task_instruction
        steps = [] if task is None else [item.model_dump(mode="json") for item in task.procedure]
        return {
            "steps": steps,
            "delivered": [
                {
                    "taught_step_id": item.get("taught_step_id"),
                    "purpose": item.get("purpose"),
                    "status": item.get("status"),
                }
                for item in self.history
                if item.get("taught_step_id")
            ],
        }

    def context(self) -> dict:
        return {
            "methods": self.library.catalog(),
            "current_purpose": self.purpose,
            "active_method_id": self.active_method_id,
            "slot_bindings": self.bindings,
            "recent_executions": self.history[-6:],
        }

    def apply(self, screen: dict) -> ScreenMatch | None:
        from .matching import ScreenMatch

        self.pending = None
        task = self.brain.task_instruction
        if not self.enabled or task is None:
            return None
        candidates = []
        completion_candidates = []
        for method in self.library.methods():
            auto_replay = bool(task.procedure) and method.task_objective == task.objective
            if method.id != self.active_method_id and not auto_replay:
                continue
            mapping = self.bindings.get(method.id)
            if mapping is None:
                if method.task_objective != task.objective:
                    continue
                mapping = {slot.name: slot.source_key for slot in method.slots}
            remaining = [
                group for group in method.action_groups if (method.id, group.id) not in self.used
            ]
            # A compiled procedure is ordered. Never skip an unbindable step and
            # accidentally execute a later screen's action.
            groups = remaining[:1] if auto_replay else remaining
            for group in groups:
                if (method.id, group.id) in self.used:
                    continue
                bound = self.library.bind_with_context(
                    method.id, mapping, screen, self.brain.values, group_id=group.id
                )
                if bound is not None:
                    candidates.append((method, mapping, bound))
            if not remaining and method.completion is not None:
                completion = self.library.bind_completion_with_context(
                    method.id, mapping, screen, self.brain.values
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
            self.purpose = response.purpose
            self.bindings[method.id] = mapping
            self.brain.log.append(
                "cache_hit",
                payload={
                    "check": "method_completion_bound",
                    "method_id": method.id,
                    "purpose": self.purpose,
                    "slot_map": mapping,
                    "evidence_fields": [item.field for item in response.evidence],
                },
            )
            return response
        if len(candidates) != 1:
            return None
        method, mapping, bound = candidates[0]
        response = ScreenMatch.model_validate(
            {
                **bound.response,
                "procedure_step": "task-action",
                "purpose": bound.response.get("purpose") or method.purpose[:160],
                "method_id": method.id,
                "taught_step_id": bound.response.get("taught_step_id"),
                "method_bindings": [
                    {"slot": slot, "source": source} for slot, source in mapping.items()
                ],
            }
        )
        self.purpose = response.purpose
        self.bindings[method.id] = mapping
        self.pending = {"method": method, "group_id": bound.group_id, "origin": "reuse"}
        self.brain.log.append(
            "cache_hit",
            payload={
                "check": "method_bound",
                "method_id": method.id,
                "group_id": bound.group_id,
                "purpose": self.purpose,
                "slot_map": mapping,
                "steps": [step.model_dump(mode="json") for step in response.steps],
            },
        )
        return response

    def remember(self, screen: dict, response: ScreenMatch) -> None:
        self.pending = None
        if not self.enabled:
            return
        task = self.brain.task_instruction
        self.purpose = response.purpose[:160]
        known = {method.id: method for method in self.library.methods()}
        previous = known.get(response.method_id)
        if response.operation == "complete":
            available = dict(known)
            available.update(
                (method_id, document.methods[0]) for method_id, document in self.staged.items()
            )
            if previous is None and task is not None:
                matches = [
                    method
                    for method in available.values()
                    if method.task_objective == task.objective
                ]
                previous = matches[0] if len(matches) == 1 else None
            if previous is None:
                return
            learned = method_completion_from_match(
                screen, response.model_dump(mode="json"), previous
            )
            if learned is not None and learned.completion is not None:
                self.staged_completions[learned.id] = learned.completion
            return
        self.active_method_id = None
        mapping = {item.slot: item.source for item in response.method_bindings}
        if previous is not None:
            mapping = (
                mapping
                or self.bindings.get(previous.id)
                or {slot.name: slot.source_key for slot in previous.slots}
            )
            if any(source not in self.brain.values for source in mapping.values()):
                return
            self.bindings[previous.id] = mapping
            self.active_method_id = previous.id
        if not response.steps or any(
            (step.source or "").startswith("credential.") for step in response.steps
        ):
            return
        purpose = self.purpose or response.expected_result
        if previous is None:
            identity = task.objective if task and task.procedure else purpose
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
            response.model_dump(mode="json"),
            method_id=method_id,
            purpose=previous.purpose if previous else purpose,
            slots=slots,
            task_objective=task.objective if task else "",
        )
        if method is not None:
            self.pending = {
                "method": method,
                "group_id": method.action_groups[0].id,
                "origin": "adaptation" if previous else "learned",
                "taught_step_id": response.taught_step_id,
            }

    def _stage(self, method) -> None:
        document = self.staged.get(method.id)
        if document is None:
            self.staged[method.id] = MethodDocument(methods=[method])
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
        self.staged[method.id] = MethodDocument(methods=[merged])

    def delivered(self, outcome: dict) -> None:
        pending, self.pending = self.pending, None
        if pending is None:
            return
        method, group_id = pending["method"], pending["group_id"]
        status = "delivered" if outcome.get("next") == "verify" else "interrupted"
        event = {
            "method_id": method.id,
            "group_id": group_id,
            "purpose": method.purpose,
            "origin": pending["origin"],
            "taught_step_id": pending.get("taught_step_id")
            or next(
                (group.taught_step_id for group in method.action_groups if group.id == group_id),
                None,
            ),
            "status": status,
            "observation_id": str(self.brain.observation.obs_id),
        }
        if status == "delivered":
            self.used.add((method.id, group_id))
            self.active_method_id = None
            if pending["origin"] != "reuse":
                method = method.model_copy(update={"source_transitions": [event["observation_id"]]})
                self._stage(method)
        self.history.append(event)
        self.brain.log.append("verify", payload={"check": "method_execution", **event})
        atomic_write_json(self.brain.case_dir / "method-progress.json", self.context())

    def finish(self, *, success: bool) -> int:
        candidates = {method_id: document.methods[0] for method_id, document in self.staged.items()}
        known = {method.id: method for method in self.library.methods()}
        for method_id, completion in self.staged_completions.items():
            method = candidates.get(method_id) or known.get(method_id)
            if method is not None:
                candidates[method_id] = method.model_copy(update={"completion": completion})
        methods = [
            compile_method_candidate(method, self.brain.values) for method in candidates.values()
        ]
        promoted = 0
        promoted_methods = []
        if success:
            for method in methods:
                promoted_methods.append(self.library.upsert(method))
                promoted += 1
            if promoted_methods:
                atomic_write_json(
                    self.brain.case_dir / "method-candidate.json",
                    # Export the full merged method. A warm run often learns only
                    # the suffix that required model fallback; that suffix alone
                    # is not a replayable business procedure.
                    MethodDocument(methods=promoted_methods),
                )
        atomic_write_json(
            self.brain.case_dir / "method-learning-result.json",
            {
                "status": "candidate" if success and methods else "incomplete",
                "business_success": success,
                "method_count": len(methods),
                "action_group_count": sum(len(item.action_groups) for item in promoted_methods),
                "completion_count": sum(item.completion is not None for item in promoted_methods),
                "promoted": promoted,
            },
        )
        return promoted
