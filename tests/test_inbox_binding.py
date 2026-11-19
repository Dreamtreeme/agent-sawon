import json
from decimal import Decimal
from pathlib import Path

import pytest

from brain.task_instruction import TaskInstruction
from models.base import MeasuredModelClient, ProviderReply, TokenUsage
from pipeline.inbox_binding import prepare_record
from sawon_runtime.storage import RunLog


@pytest.fixture
def preparation(tmp_path):
    task = TaskInstruction.model_validate(
        {
            "name": "Receive a shipment",
            "objective": "Record the supplied shipment and requery it by number",
            "identity_input_key": "reference",
            "completion_conditions": ["The receipt was verified"],
            "procedure": [
                {
                    "id": "enter",
                    "instruction": "Enter the shipment",
                    "completion": "Data is present",
                    "actions": [
                        {"verb": "fill", "target": target, "input_key": key}
                        for target, key in [
                            ("User", "credential.username"),
                            ("Gross mass (kg)", "weight"),
                            ("Receipt mass", "weight_copy"),
                            ("Confirm mass", "weight"),
                            ("Local reading", "collected_result.seen"),
                        ]
                    ],
                },
            ],
            "input_formats": {"weight": {"type": "number", "unit": "KG"}},
            "result_keys": ["seen"],
        }
    )
    return {
        "decision": {
            "work_id": "shipping",
            "facts": [
                {"key": "reference", "value": "S-41", "receipt_id": 101},
                {"key": "Gross", "value": "2,500 g", "receipt_id": 102},
                {"key": "batch_label", "value": "FYI", "receipt_id": 101},
            ],
        },
        "plan_id": 3,
        "receipts": {101: {"source": "email"}, 102: {"source": "kakao_message"}},
        "task": task,
        "log": RunLog(tmp_path / "binding", case_id="test-case", agent_id="handoff"),
        "model_config": Path("config/models.toml"),
        "env_path": None,
        "max_cost_usd": Decimal("1"),
    }


@pytest.fixture
def model(monkeypatch):
    reply = {"bindings": []}
    prompts = []

    class Client(MeasuredModelClient):
        def _generate(self, prompt, schema, *, images):
            prompts.append(prompt)
            return ProviderReply(
                text=json.dumps(reply), usage=TokenUsage(input_tokens=7, response_tokens=8)
            )

        def _cost(self, usage):
            return Decimal("0.01")

    def create_client(config_path, *, run_log, env_path):
        return Client(provider="local", model="test", run_log=run_log)

    monkeypatch.setattr("pipeline.inbox_binding.create_model_client", create_client)
    return reply["bindings"], prompts


def test_binding_selects_keys_copies_values_and_preserves_both_receipts(preparation, model):
    bindings, prompts = model
    preparation["decision"]["facts"][0]["label"] = "의뢰 번호"
    preparation["decision"]["facts"][1]["label"] = "총중량"
    bindings.extend({"input_key": key, "fact_key": "Gross"} for key in ("weight", "weight_copy"))
    record = prepare_record(**preparation)
    log = preparation["log"]
    assert record.case_id == log.case_id
    assert record.inputs == {"reference": "S-41", "weight": "2,500 g", "weight_copy": "2,500 g"}
    assert record.origin == "inbox_plan" and record.provenance.plan_id == 3
    assert record.memo_labels(preparation["task"]) == {
        "reference": "의뢰 번호",
        "weight": "총중량 · Gross mass (kg)",
        "weight_copy": "총중량 · Receipt mass",
        "credential.username": "User",
        "collected_result.seen": "Local reading",
    }
    assert record.provenance.inputs["weight"].fact_key == "Gross"
    assert record.provenance.inputs["weight"].receipt_id == 102
    proof = json.loads((log.case_dir / "input-provenance.json").read_text(encoding="utf-8"))
    assert (proof["plan_id"], proof["work_id"]) == (3, "shipping")
    assert proof["inputs"]["weight"] == {
        "fact_key": "Gross",
        "memo_label": "총중량",
        "receipt_id": 102,
        "source": "kakao_message",
        "raw_value": "2,500 g",
        "execution_value": "2.5",
    }
    assert proof["inputs"]["weight_copy"]["execution_value"] == "2,500 g"
    assert proof["inputs"]["reference"]["receipt_id"] == 101
    (prompt,) = prompts
    assert "Gross mass (kg)" in prompt and "Confirm mass" in prompt
    assert "credential." not in prompt and "collected_result." not in prompt
    (call,) = log.read_all()
    assert call.payload["context"]["purpose"] == "inbox_input_binding" and call.cost.usd == 0.01


def test_all_canonical_keys_need_no_model_configuration(preparation):
    facts = preparation["decision"]["facts"]
    facts[1]["key"] = "weight"
    facts.append({**facts[1], "key": "weight_copy"})
    preparation.update(model_config=Path("absent.toml"), max_cost_usd=Decimal(0))
    record = prepare_record(**preparation)
    assert record.inputs["weight"] == "2,500 g"
    assert record.memo_labels(preparation["task"]) == {
        "reference": "업무 식별 정보",
        "weight": "Gross mass (kg)",
        "weight_copy": "Receipt mass",
        "credential.username": "User",
        "collected_result.seen": "Local reading",
    }
    assert not preparation["log"].read_all()


@pytest.mark.parametrize(
    ("bindings", "message"),
    [
        ([("weight", "Gross")], "Missing plan facts"),
        ([("weight", "invented")], "Unknown plan fact"),
        ([("weight", "Gross"), ("weight", "batch_label")], "repeated binding"),
        ([("reference", "batch_label")], "Unexpected"),
        ([("weight", "reference"), ("weight_copy", "Gross")], "Invalid format"),
    ],
    ids=["omitted", "invented-source", "duplicate-target", "overriding-exact", "nonnumeric"],
)
def test_invalid_or_incomplete_model_binding_never_creates_manifest(
    preparation, model, bindings, message
):
    output, _ = model
    output.extend({"input_key": target, "fact_key": source} for target, source in bindings)
    with pytest.raises(ValueError, match=message):
        prepare_record(**preparation)
    assert not (preparation["log"].case_dir / "input-provenance.json").exists()


@pytest.mark.parametrize("invalid", ["foreign-receipt", "ambiguous-key", "blank", "over-budget"])
def test_preflight_rejections_precede_model_creation(preparation, monkeypatch, invalid):
    monkeypatch.setattr("pipeline.inbox_binding.create_model_client", pytest.fail)
    fact = preparation["decision"]["facts"][1]
    if invalid == "foreign-receipt":
        fact["receipt_id"] = 999
    elif invalid == "ambiguous-key":
        fact["key"] = "reference"
    elif invalid == "blank":
        fact["value"] = "   "
    else:
        preparation["max_cost_usd"] = Decimal(0)
    with pytest.raises(ValueError):
        prepare_record(**preparation)
    assert not (preparation["log"].case_dir / "input-provenance.json").exists()
