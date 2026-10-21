"""Memory Compilation: refine a successful execution trace into retrievable task memory."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
from sawon_interfaces import ShipmentRecord

from brain.cache.methods import LearnedMethod, MethodDocument, MethodSlot, method_from_match
from brain.cache.pattern_matching import ScreenPatternDocument
from brain.demo_learning.methods import read_screen
from brain.graph.screen_mapping import ScreenMapping
from brain.observe.values import values_for
from brain.task_instruction import TaskInputRecord, TaskInstruction
from models.accounting import read_model_accounting
from models.base import ModelClient
from models.budget import CallBudget
from models.factory import create_model_client
from sawon_runtime.storage import RunLog, atomic_write_json, rejection_evidence


class MemoryGroupSelection(BaseModel):
    """One successful cached action boundary selected for memory reuse."""

    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    purpose: str = Field(min_length=1, max_length=160)
    context_anchors: list[str] = Field(default_factory=list)
    expected_result: str = Field(min_length=1, max_length=240)
    review_reason: str = ""
    _discarded_context_anchors: int = PrivateAttr(default=0)

    @model_validator(mode="after")
    def bound_context_anchors(self):
        if len(self.context_anchors) > 8:
            self._discarded_context_anchors = len(self.context_anchors) - 8
            self.context_anchors = self.context_anchors[:8]
            logging.getLogger(__name__).warning(
                "warm_context_anchors_truncated retained=8 discarded=%d",
                self._discarded_context_anchors,
            )
        return self


class RemovedMemoryGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    reason: str


class MemoryAssembly(BaseModel):
    """Model selection over immutable action batches from a successful source execution."""

    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(min_length=1, max_length=300)
    groups: list[MemoryGroupSelection] = Field(min_length=1, max_length=80)
    removed_groups: list[RemovedMemoryGroup] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_candidates(self):
        ids = [group.candidate_id for group in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError("warm_method_candidate_ids_must_be_unique")
        return self


@dataclass(frozen=True)
class MemoryCandidate:
    candidate_id: str
    cache_key: str
    obs_id: str
    trace_index: int
    screen: dict
    response: dict
    observed_transitions: tuple[dict, ...] = ()
    satisfied_actions: tuple[dict, ...] = ()


MEMORY_COMPILATION_RULES = """Perform Memory Compilation as the Critic of this successful execution.
Runtime checks screen similarity first, then uses Memory Retrieval on a match or Scanning on a miss.
Both routes use Pattern Matching and Context Recall with current task inputs and observations.
Refine the supplied execution graph into memory for these runtime responsibilities.
Walk candidates in execution order: observation -> proposal -> delivered actions -> observed result.
A candidate contains only actions actually delivered; already_satisfied and nonexecuted_plans
are observation facts, not actions to replay. Do not add them back.
For each candidate, retain it in groups with review_reason, or explain its removal
in removed_groups.
Remove redundant retries and exploration only when the retained route still reaches each required
screen and task goal. Explain how the retained predecessor can reach the next retained candidate;
do not confuse unchanged pixels with an action failure, or delivery with business success.
Use observed_transitions and after_screen_ocr as result evidence. original_expected_result is only
the planner's prediction. Missing result evidence is unknown, not automatically a failed action.
Retain required business/credential inputs, login submission, save, and navigation dependencies.
The whole source run succeeded, but not every proposal was useful. Prefer effective transitions
over earlier attempts that only selected a row. Never invent/edit/duplicate an action, input source,
coordinate or marker. Return candidate IDs in increasing execution order.
The runtime reuses exact successful points under matching viewport and screenshot similarity.
context_anchors must exclude instance values, transient messages, dates and counters.
"""


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(value: dict) -> str:
    return json.dumps(
        {k: v for k, v in value.items() if k != "mapping"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _recorded_plans(case_dir: Path, events: list[dict]) -> list[dict]:
    plans = [
        {"seq": seq, **event["payload"]}
        for seq, event in enumerate(events)
        if event.get("payload", {}).get("check") == "screen_plan_proposed"
    ]
    if plans:
        return plans
    # Older runs persisted two proposal files per frame instead of each decision.
    # Locate their decision events; do not use the frame ordinal as execution order.
    for name in ("screen-match.json", "screen-plan.json"):
        for path in (case_dir / "visual").glob(f"*/{name}"):
            response = _read(path)
            matches = [
                seq
                for seq, event in enumerate(events)
                if event.get("payload", {}).get("check") == "screen_mapping"
                and Path(event["payload"].get("frame", "")).parent.name == path.parent.name
                and event["payload"].get("purpose") == response.get("purpose")
                and event["payload"].get("steps") == len(response.get("steps", []))
            ]
            plans.append(
                {
                    "seq": matches[-1] if matches else -1,
                    "obs_id": path.parent.name,
                    "response": response,
                }
            )
    return sorted(plans, key=lambda plan: plan["seq"])


def _task_contract_sha256(case_dir: Path) -> str:
    for line in (case_dir / "run_log.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        payload = event.get("payload", {})
        digest = payload.get("sha256")
        if payload.get("check") == "user_education_loaded" and isinstance(digest, str):
            if len(digest) == 64 and all(character in "0123456789abcdef" for character in digest):
                return digest
    raise ValueError("successful_run_missing_user_education_digest")


def _redact(text: str, values: dict[str, str]) -> str:
    result = text
    for key, value in sorted(values.items(), key=lambda item: len(item[1]), reverse=True):
        if value:
            result = result.replace(value, "{" + key + "}")
    return result


def _case_values(case_dir: Path) -> dict[str, str]:
    data = _read(case_dir / "record.json")
    record = (
        TaskInputRecord.model_validate(data)
        if "inputs" in data
        else ShipmentRecord.model_validate(data)
    )
    return values_for(record)


def read_memory_candidates(
    case_dir: Path, visual_cache: Path
) -> tuple[dict, list[MemoryCandidate]]:
    """Join delivered actions to successful source observations and promoted geometry."""

    case_dir = Path(case_dir)
    result = _read(case_dir / "result.json")
    if result.get("status") != "success":
        raise ValueError("warm_method_requires_successful_cold_run")
    task_path = case_dir / "task-instruction.json"
    task = TaskInstruction.model_validate_json(task_path.read_bytes())
    cache = ScreenPatternDocument.model_validate_json(Path(visual_cache).read_bytes())
    by_response: dict[str, list[str]] = {}
    for entry in cache.entries:
        by_response.setdefault(_canonical(entry.response), []).append(entry.key)
    responses = {entry.key: entry.response for entry in cache.entries}
    events = [
        json.loads(line)
        for line in (case_dir / "run_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    plans = _recorded_plans(case_dir, events)
    action_order = {
        event["payload"]["action"]["action_id"]: seq
        for seq, event in enumerate(events)
        if event.get("kind") == "act" and event.get("payload", {}).get("stage") == "requested"
    }
    transitions = {}
    satisfied = []
    for seq, event in enumerate(events):
        payload = event.get("payload", {})
        if payload.get("check") == "observed_actions_already_satisfied":
            satisfied.append((seq, payload))
        transition = payload.get("transition", {})
        if transition.get("delivery") == "delivered":
            transitions[transition["action_id"]] = (
                action_order.get(transition["action_id"], seq),
                transition,
            )
    candidates, nonexecuted = [], []
    seen = set()
    for plan in plans:
        response, obs_id = plan["response"], plan["obs_id"]
        if (
            plan.get("plan_error")
            or response.get("operation", "act") != "act"
            or not response.get("steps")
        ):
            continue
        end = next(
            (p["seq"] for p in plans if p["obs_id"] == obs_id and p["seq"] > plan["seq"]),
            len(events),
        )
        available = [
            (seq, transition)
            for seq, transition in transitions.values()
            if transition["before_obs_id"] == obs_id and plan["seq"] <= seq < end
        ]
        skipped = [
            action
            for seq, payload in satisfied
            if payload["obs_id"] == obs_id and plan["seq"] <= seq < end
            for action in payload["actions"]
        ]
        delivered, retained, already = [], [], []
        for step in response["steps"]:
            key = (step["intent"], step.get("source"))
            match = next(
                (
                    item
                    for item in available
                    if (item[1].get("intent"), item[1].get("source")) == key
                ),
                None,
            )
            if match is not None:
                available.remove(match)
                delivered.append(match)
                retained.append(step)
            elif any((a["intent"], a.get("source")) == key for a in skipped):
                already.append(step)
        if not delivered:
            nonexecuted.append(
                {
                    "obs_id": obs_id,
                    "purpose": response.get("purpose"),
                    "already_satisfied": already,
                    "replayable": False,
                }
            )
            continue
        # A partial/failed batch is not silently turned into a complete replay candidate.
        if len(retained) + len(already) != len(response["steps"]):
            continue
        delivery_ids = tuple(item[1]["action_id"] for item in delivered)
        if delivery_ids in seen:
            continue
        seen.add(delivery_ids)
        keys = by_response.get(_canonical(response))
        key = (
            keys[0]
            if keys
            else "trace:" + hashlib.sha256(_canonical(response).encode()).hexdigest()
        )
        screen = read_screen(case_dir, obs_id)
        mapping = responses[key].get("mapping") if keys else None
        if mapping is not None:
            normalized = ScreenMapping.model_validate(mapping).local_payload(screen)
            # Normalize geometry without restoring skipped actions from the provider plan.
            response = {**normalized, "operation": response.get("operation", "act")}
        response = {**response, "steps": retained}
        candidates.append(
            MemoryCandidate(
                candidate_id="",
                cache_key=key,
                obs_id=obs_id,
                trace_index=min(seq for seq, _ in delivered),
                screen=screen,
                response=response,
                observed_transitions=tuple(transition for _, transition in delivered),
                satisfied_actions=tuple(already),
            )
        )
    candidates.sort(key=lambda candidate: candidate.trace_index)
    candidates = [
        replace(candidate, candidate_id=f"c{index:03d}")
        for index, candidate in enumerate(candidates)
    ]
    if not candidates:
        raise ValueError("successful_run_has_no_joined_visual_cache_actions")
    return {
        "source_case": str(case_dir.resolve()),
        "nonexecuted_plans": nonexecuted,
        "task": task.model_dump(mode="json"),
        # Runtime scoping uses the original user-education file bytes. The case
        # copy is normalized JSON and may have a different byte hash.
        "task_sha256": _task_contract_sha256(case_dir),
        "values": _case_values(case_dir),
    }, candidates


def memory_assembly_prompt(source: dict, candidates: list[MemoryCandidate]) -> str:
    values = source["values"]
    summaries = []
    for index, candidate in enumerate(candidates):
        response = candidate.response
        raw_markers = {item["id"] for item in candidate.screen.get("markers", [])}
        observed = [
            {
                key: transition.get(key)
                for key in ("intent", "source", "observed_effect", "new_facts")
            }
            for transition in candidate.observed_transitions
        ]
        after_id = next(
            (
                item.get("after_obs_id")
                for item in reversed(candidate.observed_transitions)
                if item.get("after_obs_id")
            ),
            None,
        )
        after = {}
        if after_id and (Path(source["source_case"]) / "visual" / after_id).is_dir():
            after = read_screen(Path(source["source_case"]), after_id)
        summaries.append(
            {
                "candidate_id": candidate.candidate_id,
                "trace_index": candidate.trace_index,
                "previous_candidate_id": candidates[index - 1].candidate_id if index else None,
                "screen_kind": response.get("screen_kind", "unknown"),
                "taught_step_id": response.get("taught_step_id"),
                "ocr": [
                    _redact(str(block["text"]), values) for block in candidate.screen.get("ocr", [])
                ],
                "actions": response["steps"],
                "observed_transitions": observed,
                "already_satisfied": list(candidate.satisfied_actions),
                "after_screen_ocr": [
                    _redact(str(block["text"]), values) for block in after.get("ocr", [])
                ],
                "model_point_targets": sorted(
                    {
                        step["target"]
                        for step in response["steps"]
                        if step["target"] not in raw_markers and step.get("point") is not None
                    }
                ),
                "original_purpose": _redact(response.get("purpose", ""), values),
                "original_expected_result": _redact(response.get("expected_result", ""), values),
            }
        )
    payload = {
        "task": source["task"],
        "candidates": summaries,
        "nonexecuted_plans": [
            {**plan, "purpose": _redact(plan.get("purpose") or "", values)}
            for plan in source.get("nonexecuted_plans", [])
        ],
    }
    return MEMORY_COMPILATION_RULES + json.dumps(payload, ensure_ascii=False)


def _required_sources(candidates: list[MemoryCandidate]) -> set[str]:
    return {
        step["source"]
        for candidate in candidates
        for step in candidate.response["steps"]
        if step.get("source")
    }


def compile_memory(
    source: dict,
    candidates: list[MemoryCandidate],
    draft: MemoryAssembly | dict,
    *,
    method_id: str,
    source_receipt: str | None = None,
) -> LearnedMethod:
    """Validate the model's selection and locally compile exact cached actions."""

    assembly = draft if isinstance(draft, MemoryAssembly) else MemoryAssembly.model_validate(draft)
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    try:
        selected = [by_id[group.candidate_id] for group in assembly.groups]
    except KeyError as exc:
        raise ValueError("warm_method_selected_unknown_candidate") from exc
    indexes = [candidate.trace_index for candidate in selected]
    if indexes != sorted(indexes):
        raise ValueError("warm_method_candidates_out_of_trace_order")
    selected_sources = _required_sources(selected)
    missing_sources = _required_sources(candidates) - selected_sources
    if missing_sources:
        raise ValueError("warm_method_missing_sources:" + ",".join(sorted(missing_sources)))
    intents = {
        step["intent"] for candidate in selected for step in candidate.response.get("steps", [])
    }
    available_intents = {
        step["intent"] for candidate in candidates for step in candidate.response.get("steps", [])
    }
    for required_intent in ("login", "save"):
        if required_intent in available_intents and required_intent not in intents:
            raise ValueError("warm_method_missing_intent:" + required_intent)

    slots = [
        MethodSlot(name=source_key, description=source_key, source_key=source_key)
        for source_key in sorted(selected_sources)
    ]
    groups = []
    previous_id = None
    refs = []
    for selection, candidate in zip(assembly.groups, selected, strict=True):
        if candidate.screen.get("screen_phash") is None:
            raise ValueError("warm_method_candidate_requires_reference_screenshot")
        visible = {str(block["text"]) for block in candidate.screen.get("ocr", [])}
        context_anchors = [
            anchor
            for anchor in selection.context_anchors
            if any(anchor.casefold() in text.casefold() for text in visible)
            and not any(value and value in anchor for value in source["values"].values())
        ]
        response = {
            **candidate.response,
            "purpose": selection.purpose,
            "expected_result": selection.expected_result,
        }
        fragment = method_from_match(
            candidate.screen,
            response,
            method_id=method_id,
            purpose=assembly.purpose,
            slots=slots,
            context_anchors=context_anchors,
            task_objective=source["task"]["objective"],
            task_contract_sha256=source["task_sha256"],
            values=source["values"],
        )
        if fragment is None:
            raise ValueError(
                "warm_method_candidate_could_not_be_compiled:" + candidate.candidate_id
            )
        group = fragment.action_groups[0].model_copy(
            update={
                "trace_index": candidate.trace_index,
                "predecessor_group_ids": [] if previous_id is None else [previous_id],
            }
        )
        groups.append(group)
        previous_id = group.id
        refs.append(f"{source['source_case']}/visual/{candidate.obs_id}:{candidate.cache_key}")
    if source_receipt is not None:
        refs.append(source_receipt)
    return LearnedMethod(
        id=method_id,
        purpose=assembly.purpose,
        task_objective=source["task"]["objective"],
        task_contract_sha256=source["task_sha256"],
        artifact_kind="warm_compiled",
        slots=slots,
        action_groups=groups,
        source_transitions=refs,
    )


def assemble_memory(
    source: dict,
    candidates: list[MemoryCandidate],
    *,
    model: ModelClient,
    log: RunLog,
    method_id: str,
    output: Path,
    max_cost_usd: Decimal,
    budget: CallBudget | None = None,
) -> LearnedMethod:
    with rejection_evidence(
        log,
        "memory_compilation.assemble_memory",
        context={
            "draft": str(log.case_dir / "assembly-draft.json"),
            "input": str(log.case_dir / "assembly-input.json"),
        },
    ):
        prompt = memory_assembly_prompt(source, candidates)
        atomic_write_json(
            log.case_dir / "assembly-input.json",
            {
                "source": source,
                "candidates": [
                    {
                        "candidate_id": item.candidate_id,
                        "cache_key": item.cache_key,
                        "obs_id": item.obs_id,
                        "trace_index": item.trace_index,
                        "screen": item.screen,
                        "response": item.response,
                        "observed_transitions": item.observed_transitions,
                        "already_satisfied": item.satisfied_actions,
                    }
                    for item in candidates
                ],
            },
        )
        atomic_write_json(log.case_dir / "assembly-request.json", {"prompt": prompt})
        accounting = read_model_accounting(log)
        if accounting.unknown:
            raise ValueError("unknown_model_call_cost")
        reserved = budget(prompt, MemoryAssembly) if budget is not None else Decimal(0)
        if accounting.known_usd + reserved > max_cost_usd:
            raise ValueError("warm_method_assembly_cost_cap")
        draft = model.complete_json(prompt, MemoryAssembly).response
        for index, group in enumerate(draft.groups):
            if group._discarded_context_anchors:
                log.append(
                    "verify",
                    payload={
                        "check": "warm_context_anchors_truncated",
                        "level": "warning",
                        "group_index": index,
                        "retained": 8,
                        "discarded": group._discarded_context_anchors,
                    },
                )
        draft_path = log.case_dir / "assembly-draft.json"
        atomic_write_json(draft_path, draft)
        method = compile_memory(
            source,
            candidates,
            draft,
            method_id=method_id,
            source_receipt=str(draft_path.resolve()),
        )
        atomic_write_json(output, MethodDocument(methods=[method]))
        log.append(
            "verify",
            payload={
                "check": "warm_method_compiled",
                "method_id": method.id,
                "groups": len(method.action_groups),
                "sources": [slot.source_key for slot in method.slots],
                "output": str(output.resolve()),
            },
        )
        return method


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-case", type=Path, required=True)
    parser.add_argument("--visual-cache", type=Path, required=True)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--method-id", default="memory-" + uuid4().hex[:12])
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--env-path", type=Path)
    parser.add_argument("--max-cost-usd", type=float, required=True)
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_cost_usd) or not 0 < args.max_cost_usd <= 50:
        parser.error("A finite per-compilation budget up to $50 is required")
    source, candidates = read_memory_candidates(args.source_case, args.visual_cache)
    args.case_dir.mkdir(parents=True, exist_ok=False)
    log = RunLog(args.case_dir, case_id="memory-compile-" + uuid4().hex, agent_id="memory-compiler")
    summary = {"status": "stopped", "output": str(args.output.resolve())}
    exit_code = 1
    try:
        model = create_model_client(args.model_config, run_log=log, env_path=args.env_path)
        method = assemble_memory(
            source,
            candidates,
            model=model,
            log=log,
            method_id=args.method_id,
            output=args.output,
            max_cost_usd=Decimal(str(args.max_cost_usd)),
            budget=CallBudget.from_config(args.model_config),
        )
        summary.update(status="compiled", method_id=method.id, groups=len(method.action_groups))
        exit_code = 0
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
    finally:
        accounting = read_model_accounting(log)
        summary.update(
            model_calls=accounting.model_calls,
            known_usd=str(accounting.known_usd),
            unknown_cost=accounting.unknown,
        )
        atomic_write_json(args.case_dir / "result.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
