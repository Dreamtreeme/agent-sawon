"""Compile one successful cold run and its visual cache into a warm method."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sawon_interfaces import ShipmentRecord

from brain.cache.methods import LearnedMethod, MethodDocument, MethodSlot, method_from_match
from brain.cache.visual_match import VisualMatchDocument
from brain.demo_learning.methods import read_screen
from brain.observe.values import values_for
from brain.task_instruction import TaskInputRecord, TaskInstruction
from models.accounting import read_model_accounting
from models.base import ModelClient
from models.budget import CallBudget
from models.factory import create_model_client
from sawon_runtime.storage import RunLog, atomic_write_json


class WarmGroupSelection(BaseModel):
    """One successful cached action boundary selected for warm replay."""

    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    purpose: str = Field(min_length=1, max_length=160)
    context_anchors: list[str] = Field(default_factory=list, max_length=8)
    expected_result: str = Field(min_length=1, max_length=240)


class WarmMethodAssembly(BaseModel):
    """Model selection over immutable successful cold-run action batches."""

    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(min_length=1, max_length=160)
    groups: list[WarmGroupSelection] = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def unique_candidates(self):
        ids = [group.candidate_id for group in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError("warm_method_candidate_ids_must_be_unique")
        return self


@dataclass(frozen=True)
class WarmCandidate:
    candidate_id: str
    cache_key: str
    obs_id: str
    trace_index: int
    screen: dict
    response: dict


ASSEMBLY_RULES = """Build a reusable warm GUI method from successful cold-run cache entries.
The entire source run reached business success. Each candidate is an immutable action batch from
that run: select candidates and stable screen context only; never invent, edit, or duplicate an
action, target, input source, coordinate, or marker. Preserve increasing trace order. Remove only
retries and exploratory actions whose effect is represented by a later successful candidate.
Retain every business and credential input source, the command that submits login, the final save
command, and navigation needed to reach those actions. A screen-relative model point is a valid
learned target when it succeeded in the cold run. For every selected candidate containing such a
point, context_anchors must contain one or more stable OCR strings visible on that screen that
distinguish the screen; exclude case values, transient messages, dates, and counters. For detected
targets, anchors may be empty when labels already distinguish them. Describe only the selected
batch's purpose and expected immediate result. Return candidate IDs exactly as supplied.
"""


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _observation_order(case_dir: Path) -> dict[str, int]:
    order: dict[str, int] = {}
    log_path = case_dir / "run_log.jsonl"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            obs_id = event.get("payload", {}).get("obs_id")
            if event.get("kind") == "observe" and obs_id and obs_id not in order:
                order[obs_id] = len(order)
    return order


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


def read_warm_candidates(case_dir: Path, visual_cache: Path) -> tuple[dict, list[WarmCandidate]]:
    """Join promoted cache responses to their successful cold-run screens in trace order."""

    case_dir = Path(case_dir)
    result = _read(case_dir / "result.json")
    if result.get("status") != "success":
        raise ValueError("warm_method_requires_successful_cold_run")
    task_path = case_dir / "task-instruction.json"
    task = TaskInstruction.model_validate_json(task_path.read_bytes())
    cache = VisualMatchDocument.model_validate_json(Path(visual_cache).read_bytes())
    by_response: dict[str, list[str]] = {}
    for entry in cache.entries:
        by_response.setdefault(_canonical(entry.response), []).append(entry.key)
    order = _observation_order(case_dir)
    paths = list((case_dir / "visual").glob("*/screen-match.json"))
    paths.sort(key=lambda path: (order.get(path.parent.name, 10**9), path.parent.name))
    candidates = []
    for path in paths:
        response = _read(path)
        if response.get("operation", "act") != "act" or not response.get("steps"):
            continue
        keys = by_response.get(_canonical(response))
        if not keys:
            continue
        index = order.get(path.parent.name, len(order) + len(candidates))
        candidates.append(
            WarmCandidate(
                candidate_id=f"c{len(candidates):03d}",
                cache_key=keys[0],
                obs_id=path.parent.name,
                trace_index=index,
                screen=read_screen(case_dir, path.parent.name),
                response=response,
            )
        )
    if not candidates:
        raise ValueError("successful_run_has_no_joined_visual_cache_actions")
    return {
        "source_case": str(case_dir.resolve()),
        "task": task.model_dump(mode="json"),
        # Runtime scoping uses the original user-education file bytes. The case
        # copy is normalized JSON and may have a different byte hash.
        "task_sha256": _task_contract_sha256(case_dir),
        "values": _case_values(case_dir),
    }, candidates


def assembly_prompt(source: dict, candidates: list[WarmCandidate]) -> str:
    values = source["values"]
    summaries = []
    for candidate in candidates:
        response = candidate.response
        raw_markers = {item["id"] for item in candidate.screen.get("markers", [])}
        summaries.append(
            {
                "candidate_id": candidate.candidate_id,
                "trace_index": candidate.trace_index,
                "screen_kind": response.get("screen_kind", "unknown"),
                "taught_step_id": response.get("taught_step_id"),
                "ocr": [
                    _redact(str(block["text"]), values) for block in candidate.screen.get("ocr", [])
                ],
                "actions": response["steps"],
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
    payload = {"task": source["task"], "candidates": summaries}
    return ASSEMBLY_RULES + json.dumps(payload, ensure_ascii=False)


def _required_sources(candidates: list[WarmCandidate]) -> set[str]:
    return {
        step["source"]
        for candidate in candidates
        for step in candidate.response["steps"]
        if step.get("source")
    }


def compile_warm_method(
    source: dict,
    candidates: list[WarmCandidate],
    draft: WarmMethodAssembly | dict,
    *,
    method_id: str,
    source_receipt: str | None = None,
) -> LearnedMethod:
    """Validate the model's selection and locally compile exact cached actions."""

    assembly = (
        draft if isinstance(draft, WarmMethodAssembly) else WarmMethodAssembly.model_validate(draft)
    )
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
        raw_ids = {item["id"] for item in candidate.screen.get("markers", [])}
        has_model_point = any(
            step["target"] not in raw_ids for step in candidate.response.get("steps", [])
        )
        visible = {str(block["text"]) for block in candidate.screen.get("ocr", [])}
        if has_model_point and not selection.context_anchors:
            raise ValueError("warm_method_point_target_requires_context_anchor")
        if any(
            not any(anchor.casefold() in text.casefold() for text in visible)
            for anchor in selection.context_anchors
        ):
            raise ValueError("warm_method_context_anchor_not_visible")
        if any(
            value and value in anchor
            for anchor in selection.context_anchors
            for value in source["values"].values()
        ):
            raise ValueError("warm_method_context_anchor_contains_instance_value")
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
            context_anchors=selection.context_anchors,
            task_objective=source["task"]["objective"],
            task_contract_sha256=source["task_sha256"],
        )
        if fragment is None:
            raise ValueError("warm_method_candidate_could_not_be_compiled")
        compiled_targets = []
        for target in fragment.action_groups[0].targets:
            if target.screen_relative_point is None:
                compiled_targets.append(target)
                continue
            anchors = []
            for anchor in selection.context_anchors:
                found = [
                    block
                    for block in candidate.screen.get("ocr", [])
                    if anchor.casefold() in str(block["text"]).casefold()
                ]
                if len(found) != 1:
                    raise ValueError("warm_method_point_anchor_must_be_unique")
                anchors.append(found[0])
            left = min(item["bbox"][0] for item in anchors)
            top = min(item["bbox"][1] for item in anchors)
            right = max(item["bbox"][0] + item["bbox"][2] for item in anchors)
            bottom = max(item["bbox"][1] + item["bbox"][3] for item in anchors)
            point = (
                target.screen_relative_point[0] * candidate.screen["image_size"][0],
                target.screen_relative_point[1] * candidate.screen["image_size"][1],
            )
            compiled_targets.append(
                target.model_copy(
                    update={
                        "anchor_texts": [str(item["text"]) for item in anchors],
                        "screen_relative_point": None,
                        "anchor_relative_point": (
                            (point[0] - left) / max(1, right - left),
                            (point[1] - top) / max(1, bottom - top),
                        ),
                    }
                )
            )
        group = fragment.action_groups[0].model_copy(
            update={
                "targets": compiled_targets,
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


def assemble_warm_method(
    source: dict,
    candidates: list[WarmCandidate],
    *,
    model: ModelClient,
    log: RunLog,
    method_id: str,
    output: Path,
    max_cost_usd: Decimal,
    budget: CallBudget | None = None,
) -> LearnedMethod:
    prompt = assembly_prompt(source, candidates)
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
                }
                for item in candidates
            ],
        },
    )
    atomic_write_json(log.case_dir / "assembly-request.json", {"prompt": prompt})
    accounting = read_model_accounting(log)
    if accounting.unknown:
        raise ValueError("unknown_model_call_cost")
    reserved = budget(prompt, WarmMethodAssembly) if budget is not None else Decimal(0)
    if accounting.known_usd + reserved > max_cost_usd:
        raise ValueError("warm_method_assembly_cost_cap")
    draft = model.complete_json(prompt, WarmMethodAssembly).response
    draft_path = log.case_dir / "assembly-draft.json"
    atomic_write_json(draft_path, draft)
    method = compile_warm_method(
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
    parser.add_argument("--method-id", default="warm-" + uuid4().hex[:12])
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--env-path", type=Path)
    parser.add_argument("--max-cost-usd", type=float, required=True)
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_cost_usd) or not 0 < args.max_cost_usd <= 50:
        parser.error("A finite per-compilation budget up to $50 is required")
    source, candidates = read_warm_candidates(args.source_case, args.visual_cache)
    args.case_dir.mkdir(parents=True, exist_ok=False)
    log = RunLog(args.case_dir, case_id="warm-compile-" + uuid4().hex, agent_id="warm-compiler")
    summary = {"status": "stopped", "output": str(args.output.resolve())}
    exit_code = 1
    try:
        model = create_model_client(args.model_config, run_log=log, env_path=args.env_path)
        method = assemble_warm_method(
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
