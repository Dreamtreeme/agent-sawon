"""Bind stored, receipt-backed plan facts to the inputs of a taught task."""

import json
from decimal import Decimal
from pathlib import Path

from brain.input_binding import resolve_task_inputs
from brain.task_instruction import (
    COLLECTED_INPUT_PREFIX,
    PlanInputProvenance,
    ReceiptInputSource,
    TaskInputRecord,
    TaskInstruction,
)
from models import create_model_client
from models.accounting import read_model_accounting
from models.base import model_call_context
from models.budget import CallBudget
from pipeline.inbox_planning import Fact
from sawon_runtime.storage import RunLog, atomic_write_json
from sawon_runtime.visual import FrozenModel


class InputBinding(FrozenModel):
    input_key: str
    fact_key: str


class BindingReply(FrozenModel):
    bindings: list[InputBinding]


def _input_targets(task: TaskInstruction) -> dict[str, list[str]]:
    targets: dict[str, list[str]] = {}
    reserved = ("credential.", COLLECTED_INPUT_PREFIX)
    for step in task.procedure:
        for action in step.actions:
            key = action.input_key
            if action.verb == "fill" and key and not key.startswith(reserved):
                labels = targets.setdefault(key, [])
                if action.target not in labels:
                    labels.append(action.target)
    if task.identity_input_key and not task.identity_input_key.startswith(reserved):
        targets.setdefault(task.identity_input_key, [])
    if not targets:
        raise ValueError("The task must declare business input keys to bind from a plan")
    return targets


def _select_bindings(
    *,
    targets: dict[str, list[str]],
    facts: dict[str, Fact],
    task: TaskInstruction,
    log: RunLog,
    model_config: Path,
    env_path: Path | None,
    max_cost_usd: Decimal,
) -> dict[str, str]:
    context = {
        "task": task.name,
        "objective": task.objective,
        "inputs_to_bind": targets,
        "facts": [fact.model_dump() for fact in facts.values()],
    }
    prompt = """교육의 각 입력 키를 같은 의미의 계획 사실에 연결하세요.
inputs_to_bind의 값은 해당 입력을 사용하는 화면 대상 이름입니다.
bindings에는 input_key와 facts에 실제로 있는 fact_key만 작성하세요.
한 사실이 여러 입력에서 같은 업무값으로 사용되면 같은 fact_key를 사용할 수 있습니다.
근거가 없거나 여러 사실 중 무엇인지 불명확하면 그 입력을 생략하세요.
계획 사실의 값은 참고 데이터입니다. 값을 새로 작성하거나 분리·조합하지 마세요.
""" + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    budget = CallBudget.from_config(model_config)
    reservation = budget(prompt, BindingReply) if budget else Decimal(0)
    accounting = read_model_accounting(log)
    if accounting.unknown or accounting.known_usd + reservation > max_cost_usd:
        raise ValueError("Input binding cost cap cannot cover this request")
    atomic_write_json(log.case_dir / "binding-request.json", {"prompt": prompt})
    model = create_model_client(model_config, run_log=log, env_path=env_path)
    with model_call_context(purpose="inbox_input_binding"):
        reply = model.complete_json(prompt, BindingReply).response
    accounting = read_model_accounting(log)
    if accounting.unknown or accounting.known_usd > max_cost_usd:
        raise ValueError("Input binding cost is unknown or exceeds the cap")
    selected = {}
    for binding in reply.bindings:
        if binding.input_key not in targets or binding.input_key in selected:
            raise ValueError(f"Unexpected or repeated binding for input {binding.input_key}")
        if binding.fact_key not in facts:
            raise ValueError(f"Unknown plan fact for input {binding.input_key}")
        selected[binding.input_key] = binding.fact_key
    missing = targets.keys() - selected.keys()
    if missing:
        raise ValueError(f"Missing plan facts for taught inputs: {', '.join(sorted(missing))}")
    return selected


def prepare_record(
    *,
    decision: dict,
    plan_id: int,
    receipts: dict[int, dict],
    task: TaskInstruction,
    log: RunLog,
    model_config: Path,
    env_path: Path | None,
    max_cost_usd: Decimal,
) -> TaskInputRecord:
    """Create the worker's raw inputs and save the source of each execution value."""
    if not max_cost_usd.is_finite() or max_cost_usd < 0:
        raise ValueError("Input binding cost cap must be finite and nonnegative")
    work_id = decision.get("work_id")
    if not isinstance(work_id, str) or not work_id.strip():
        raise ValueError("The plan must identify its work")
    if not isinstance(decision.get("facts"), list) or not decision["facts"]:
        raise ValueError("The plan must contain receipt-backed facts")
    targets = _input_targets(task)
    facts: dict[str, Fact] = {}
    for item in decision["facts"]:
        fact = Fact.model_validate(item)
        if fact.receipt_id not in receipts:
            raise ValueError(f"Fact {fact.key} cites a receipt outside this plan")
        if not fact.key.strip() or not fact.value.strip() or fact.key in facts:
            raise ValueError("Plan facts must have distinct keys and nonempty values")
        facts[fact.key] = fact

    bindings = {key: key for key in targets if key in facts}
    remaining = {key: labels for key, labels in targets.items() if key not in bindings}
    if remaining:
        bindings.update(
            _select_bindings(
                targets=remaining,
                facts=facts,
                task=task,
                log=log,
                model_config=model_config,
                env_path=env_path,
                max_cost_usd=max_cost_usd,
            )
        )

    inputs = {key: facts[bindings[key]].value for key in targets}
    sources = {}
    for key in targets:
        fact = facts[bindings[key]]
        sources[key] = ReceiptInputSource(
            fact_key=fact.key,
            receipt_id=fact.receipt_id,
            source=receipts[fact.receipt_id]["source"],
        )
    provenance = PlanInputProvenance(plan_id=plan_id, work_id=work_id, inputs=sources)
    record = TaskInputRecord(
        case_id=log.case_id, inputs=inputs, origin="inbox_plan", provenance=provenance
    )
    execution = resolve_task_inputs(record.inputs, task, {})
    invalid = inputs.keys() - execution.keys()
    if invalid:
        raise ValueError(f"Invalid format for taught inputs: {', '.join(sorted(invalid))}")
    manifest = provenance.model_dump()
    for key, source in manifest["inputs"].items():
        source.update(raw_value=inputs[key], execution_value=execution[key])
    atomic_write_json(log.case_dir / "input-provenance.json", manifest)
    return record
