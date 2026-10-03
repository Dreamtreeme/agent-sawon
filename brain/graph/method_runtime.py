"""Use learned methods before screen reasoning and retain delivered local adaptations."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from brain.cache.methods import MethodDocument, MethodLibrary, MethodSlot, method_from_match
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
        for method in self.library.methods():
            if method.id != self.active_method_id:
                continue
            mapping = self.bindings.get(method.id)
            if mapping is None:
                if method.task_objective != task.objective:
                    continue
                mapping = {slot.name: slot.source_key for slot in method.slots}
            for group in method.action_groups:
                if (method.id, group.id) in self.used:
                    continue
                bound = self.library.bind_with_context(
                    method.id, mapping, screen, self.brain.values, group_id=group.id
                )
                if bound is not None:
                    candidates.append((method, mapping, bound))
        if len(candidates) != 1:
            return None
        method, mapping, bound = candidates[0]
        response = ScreenMatch.model_validate(
            {
                **bound.response,
                "procedure_step": "task-action",
                "purpose": method.purpose[:160],
                "method_id": method.id,
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
        self.purpose = response.purpose[:160]
        known = {method.id: method for method in self.library.methods()}
        previous = known.get(response.method_id)
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
        # The first executable method family is business data entry. A later model
        # decision can still navigate/select freely using the existing executor.
        if not response.steps or not all(
            step.action == "fill" and step.source in self.brain.values for step in response.steps
        ):
            return
        purpose = self.purpose or response.expected_result
        if previous is None:
            identity = purpose + "\n" + "\n".join(sorted(s.source for s in response.steps))
            method_id = "method-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
            slots = [
                MethodSlot(name=source, description=source, source_key=source)
                for source in dict.fromkeys(step.source for step in response.steps)
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
                for source in dict.fromkeys(step.source for step in response.steps)
                if source not in covered
            )
        task = self.brain.task_instruction
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
            }

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
            "status": status,
            "observation_id": str(self.brain.observation.obs_id),
        }
        if status == "delivered":
            self.used.add((method.id, group_id))
            self.active_method_id = None
            if pending["origin"] != "reuse":
                method = method.model_copy(update={"source_transitions": [event["observation_id"]]})
                self.library.upsert(method)
        self.history.append(event)
        self.brain.log.append("verify", payload={"check": "method_execution", **event})
        atomic_write_json(self.brain.case_dir / "method-progress.json", self.context())
