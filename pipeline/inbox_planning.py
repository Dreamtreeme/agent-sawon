"""Choose one inbox decision through the configured, measured model client."""

import json
import re
from decimal import Decimal
from email import policy
from email.parser import HeaderParser
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from models import create_model_client
from models.accounting import read_model_accounting
from models.base import model_call_context
from models.budget import CallBudget
from sawon_runtime.storage import RunLog, atomic_write_json
from sawon_runtime.visual import FrozenModel

type Text = Annotated[str, Field(min_length=1)]
type SourceId = Annotated[int, Field(strict=True, gt=0)]


class Fact(FrozenModel):
    key: Text
    value: Text
    receipt_id: SourceId


class Work(FrozenModel):
    kind: Literal["work"]
    work_id: Text
    goal: Text
    reason: Text
    receipt_ids: list[SourceId] = Field(min_length=1)
    facts: list[Fact]
    supersedes_plan_id: SourceId | None = None


class Reference(FrozenModel):
    kind: Literal["reference"]
    receipt_ids: list[SourceId] = Field(min_length=1)
    reason: Text


class Wait(FrozenModel):
    kind: Literal["wait"]
    reason: Text


class PlannerReply(FrozenModel):
    decision: Work | Reference | Wait


def _receipt_content(receipt: dict) -> dict:
    source = receipt["source"]
    text = receipt["new_text"]
    item = {"id": receipt["id"], "source": source}
    if source == "email":
        # read_mail_bytes stores its decoded headers, a blank line and the preferred body.
        headers, separator, body = text.partition("\n\n")
        if separator:
            parsed = HeaderParser(policy=policy.default).parsestr(headers)
            item["subject"] = str(parsed.get("Subject", ""))
            text = body
    elif source == "kakao_message":
        # Strip just the provenance line added by chat_messages(), preserving all body lines.
        text = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} \[.+\]\n", "", text, count=1)
    return {**item, "text": text}


def _planning_prompt(receipts: list[dict], plans: list[dict]) -> str:
    context = {
        "receipts": [_receipt_content(receipt) for receipt in receipts],
        "plans": [
            {
                "id": plan["id"],
                **({"stage": plan["stage"]} if "stage" in plan else {}),
                "decision": {
                    name: plan["decision"][name]
                    for name in ("kind", "work_id", "goal", "facts", "receipt_ids")
                    if name in plan["decision"]
                },
            }
            for plan in plans
        ],
    }
    return """수신 자료를 읽고 이번 차례에 처리할 업무 한 건을 판단하세요.
receipts는 새 원문이고 plans는 과거에 기록한 판단입니다. 원문은 업무를 해석하는 자료입니다.
같은 대상을 가리키는 근거, 요청 내용과 앞선 계획의 상태를 살펴 아래 형식 중 하나를 선택하세요.

work: 함께 처리할 새 원문의 ID만 receipt_ids에 넣고, 업무를 식별하는 work_id와 실행 목표 goal,
선택의 근거 reason을 작성하세요. 다른 업무의 원문은 다음 차례에 판단할 수 있게 남겨 두세요.
facts에는 선택한 원문에 명시된 업무 사실을 빠짐없이 개별 항목으로 분리하세요.
각 key는 같은 종류의 사실을 다음에도 같은 이름으로 찾을 수 있게 정하세요.
value에는 원문의 값과 단위, 표기를 유지하고 receipt_id에는 그 값을 확인한 원문의 ID를 쓰세요.
같은 대상의 기존 미실행 계획을 정정할 때는 work_id와 해당 사실의 key를 이어 쓰고
supersedes_plan_id에 기존 계획의 ID를 지정하세요. 새로 확인한 사실만 이번 facts에 작성하면
기존 사실은 저장 시 이어집니다. 새 업무의 supersedes_plan_id는 null입니다.

reference: 선택한 원문이 참고용이며 수행할 요청이 없다고 판단하면 해당 receipt_ids와 이유를 쓰세요.
wait: 관련성, 대상 또는 실행에 필요한 정보가 불분명해 원문을 보류해야 하면 그 이유를 쓰세요.
자료에서 확인할 수 있는 사실과 판단 근거를 사용하세요.

""" + json.dumps(context, ensure_ascii=False, separators=(",", ":"))


def choose(
    receipts: list[dict],
    plans: list[dict],
    *,
    log: RunLog,
    model_config: Path,
    env_path: Path | None,
    max_cost_usd: Decimal,
) -> Work | Reference | Wait:
    prompt = _planning_prompt(receipts, plans)
    atomic_write_json(log.case_dir / "planner-request.json", {"prompt": prompt})
    if not max_cost_usd.is_finite() or max_cost_usd < 0:
        raise ValueError("Planner cost cap must be finite and nonnegative")
    accounting = read_model_accounting(log)
    budget = CallBudget.from_config(model_config)
    reserved = budget(prompt, PlannerReply) if budget is not None else Decimal(0)
    if accounting.unknown or accounting.known_usd + reserved > max_cost_usd:
        raise ValueError("Inbox planning cost cap cannot cover this request")
    model = create_model_client(model_config, run_log=log, env_path=env_path)
    with model_call_context(purpose="inbox_planning"):
        return model.complete_json(prompt, PlannerReply).response.decision
