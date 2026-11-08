"""Synthetic two-application readings; no live messages, desktop or model calls."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from runtime_profile import current_settings
from test_brain import FakeRelay, ScriptedModel

from brain.graph.context_recall import recall_context
from brain.graph.engine import Brain
from brain.graph.schemas import DecisionResponse
from brain.graph.screen_mapping import ScreenMapping
from brain.graph.screen_memory import collected_result, remember_screen
from brain.task_instruction import TaskInputRecord, TaskInstruction
from sawon_runtime.storage import RunLog


def reading(path, values):
    """Existing model mapping contract with deliberately synthetic application data."""
    return ScreenMapping(
        screen_path=path,
        fields={
            source: {"role": "text", "bbox": [10, 10, 100, 20], "source": source, "value": value}
            for source, value in values.items()
        },
    )


def test_synthetic_two_app_readings_complete_without_supplied_business_values(tmp_path):
    chat = {"order.reference": "SYNTHETIC-17", "quantity": "12"}
    mail = {"product.name": "Sample boxes", "due_date": "2026-11-20", "address": "Demo site"}
    expected = {**chat, **mail}
    task = TaskInstruction(
        name="Synthetic information collection",
        objective="Read the specified chat and email, then combine the requested facts locally.",
        result_keys=list(expected),
        completion_conditions=["All requested facts have been read from the two applications."],
    )
    assert TaskInputRecord(case_id="empty-collection", inputs={}).inputs == {}
    # Search parameters locate the source; none contains a business answer.
    supplied = {"query.email": 'subject:"Synthetic order"'}
    record = TaskInputRecord(case_id="synthetic-collection", inputs=supplied)
    relay = FakeRelay(record)
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="synthetic-collection-test")
    brain = Brain(
        record=record,
        goal=task.objective,
        relay=relay,
        model=ScriptedModel(log, []),
        settings=current_settings(tmp_path, task_instruction=task),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    brain.phase = "task"
    brain.observation = relay.observe()
    brain.observation_number = 1
    brain._remember_model_task_evidence(
        SimpleNamespace(
            mapping=reading(["Chat", "Synthetic contact"], chat), observed_step_results=[]
        )
    )
    request = brain._screen_decision_request({}, None)
    context = recall_context(request, projected_screen={})["notebook"]
    assert context["inputs"] == supplied
    assert context["remaining_inputs"] == list(supplied)
    assert context["collected_result"]["values"] == chat
    assert context["collected_result"]["missing_keys"] == list(mail)
    complete = DecisionResponse.model_validate(
        {
            "decision": {
                "screen_kind": "query",
                "operation": "complete",
                "intent": "verify",
                "subgoal": "Combine the collected facts",
                "reason": "Synthetic completion proposal",
                "action": None,
                "evidence": [],
                "completion_evidence": [],
            }
        }
    ).to_decision()
    brain._check_complete(complete)
    assert brain.result is None, "A missing email reading must not become task success"
    brain.observation = relay.observe()
    brain.observation_number = 2
    brain._remember_model_task_evidence(
        SimpleNamespace(
            mapping=reading(["Mail", "Synthetic order"], mail), observed_step_results=[]
        )
    )
    assert brain._check_complete(complete) == "finish"
    result = json.loads((tmp_path / "task-result.json").read_text(encoding="utf-8"))
    assert result["status"] == "success"
    assert result["values"] == expected
    assert result["missing_keys"] == []
    assert result["provenance"]["quantity"][0]["screen_path"] == ["Chat", "Synthetic contact"]
    assert result["provenance"]["product.name"][0]["screen_path"] == ["Mail", "Synthetic order"]
    assert all(
        item["observation_id"] and item["observation_number"] in {1, 2}
        for readings in result["provenance"].values()
        for item in readings
    )
    assert brain.values == supplied
    assert brain._observed_inputs() == {}
    assert not relay.actions
    assert not any(event.kind == "model_call" for event in log.read_all())


def test_synthetic_collected_result_preserves_provenance_and_explicit_blank():
    nodes = {}
    keys = ["reference"]
    remember_screen(
        nodes,
        reading(["Chat"], {"reference": "FIRST", "unrequested": "IGNORED"}),
        {},
        "frame-1",
        observation_number=1,
        result_keys=keys,
    )
    remember_screen(
        nodes,
        reading(["Mail"], {"reference": "UPDATED"}),
        {},
        "frame-2",
        observation_number=2,
        result_keys=keys,
    )
    result = collected_result(nodes, keys)
    assert result["values"] == {"reference": "UPDATED"}
    assert [item["value"] for item in result["provenance"]["reference"]] == ["FIRST", "UPDATED"]
    assert "unrequested" not in nodes['["Chat"]']["fields"]
    # Omission keeps the actual reading; an explicit blank supersedes it.
    remember_screen(
        nodes,
        ScreenMapping(screen_path=["Mail"], fields={}),
        {},
        "frame-3",
        observation_number=3,
        result_keys=keys,
    )
    assert collected_result(nodes, keys)["values"] == {"reference": "UPDATED"}
    remember_screen(
        nodes,
        reading(["Mail"], {"reference": ""}),
        {},
        "frame-4",
        observation_number=4,
        result_keys=keys,
    )
    result = collected_result(nodes, keys)
    assert result["values"] == {}
    assert result["missing_keys"] == keys
    assert result["provenance"]["reference"][-1]["observation_id"] == "frame-4"


def test_collection_result_keys_cannot_export_credential_sources():
    with pytest.raises(ValidationError, match="task_input_key_invalid"):
        TaskInstruction(
            name="Invalid collection",
            objective="Synthetic invalid configuration",
            completion_conditions=["Done"],
            result_keys=["credential.password"],
        )


def test_synthetic_graph_reads_mail_switches_to_chat_and_exports_combined_result(tmp_path):
    """Run the real worker graph with existing fake perception, model and window relay.

    The values below are synthetic model replies, never inputs to a live worker.
    Both windows start open; this verifies handoff and collection, not real search quality.
    """
    from test_window_actions import setup_window_brain

    from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES

    mail = {"order.reference": "SYNTHETIC-42", "product.name": "Sample box", "quantity": "8"}
    chat = {"due_date": "2026-11-21", "address": "Synthetic destination"}
    expected = {**mail, **chat}
    task = TaskInstruction(
        name="Synthetic mail and chat collection",
        objective="Combine the requested facts from the specified email and chat.",
        result_keys=list(expected),
        completion_conditions=["Every requested fact has been collected from the two sources."],
    )
    mail_response = reading(["Mail", "Synthetic order"], mail).model_dump()
    mail_response.update(
        screen_kind="query",
        steps=[{"action": "focus_window", "handle": 2, "intent": "navigate"}],
        purpose="Read the remaining facts in the chat window",
    )
    chat_response = reading(["Chat", "Synthetic contact"], chat).model_dump()
    chat_response.update(screen_kind="query", operation="complete")
    record = TaskInputRecord(
        case_id="synthetic-graph-collection",
        inputs={"query.email": "Synthetic order", "query.chat": "Synthetic contact"},
    )
    brain, model, relay, sent = setup_window_brain(
        tmp_path, record, [mail_response, chat_response], task_instruction=task
    )

    outcome = brain.run()

    assert outcome.status == "success", [event.payload for event in brain.log.read_all()[-8:]]
    assert sent == [2]
    assert relay.current.active_window.handle == 2
    receipts = [
        event.payload
        for event in brain.log.read_all()
        if event.kind == "act" and event.payload.get("stage") == "returned"
    ]
    assert len(receipts) == 1 and receipts[0]["ok"] is True
    assert receipts[0]["post_obs_id"] is not None and receipts[0]["error"] is None
    assert len(model.prompts) == 2
    contexts = [json.loads(prompt[len(CONTEXT_MAPPING_RULES) :]) for prompt in model.prompts]
    assert contexts[0]["notebook"]["collected_result"]["values"] == {}
    assert contexts[1]["notebook"]["collected_result"]["values"] == mail
    assert contexts[1]["notebook"]["collected_result"]["missing_keys"] == list(chat)
    assert [context["screen"]["active_window"]["handle"] for context in contexts] == [1, 2]
    result = json.loads((tmp_path / "task-result.json").read_text(encoding="utf-8"))
    assert result["status"] == "success"
    assert result["values"] == expected
    assert result["missing_keys"] == []
    assert result["provenance"]["order.reference"][0]["screen_path"] == ["Mail", "Synthetic order"]
    assert result["provenance"]["address"][0]["screen_path"] == ["Chat", "Synthetic contact"]
    assert brain.values == record.inputs
    assert brain._observed_inputs() == {}
