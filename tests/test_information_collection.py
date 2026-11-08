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


def synthetic_quantity_transfer_task():
    """The synthetic manual names the source of the value, never its answer."""
    return TaskInstruction(
        name="Synthetic mail quantity to spreadsheet",
        objective="Read the order quantity from mail and save it in the spreadsheet.",
        result_keys=["quantity"],
        completion_conditions=["The quantity read from mail is saved in the spreadsheet."],
        procedure=[
            {
                "id": "read-mail",
                "instruction": "Read the quantity in the specified order email.",
                "completion": "The order quantity has been read from the email.",
                "actions": [{"verb": "verify", "target": "Order quantity"}],
            },
            {
                "id": "update-sheet",
                "instruction": "Enter the quantity read from mail in the quantity cell and save.",
                "completion": "The spreadsheet has saved the quantity read from mail.",
                "actions": [
                    {
                        "verb": "fill",
                        "target": "Quantity cell",
                        "input_key": "collected_result.quantity",
                        "surface": "table_cell",
                    },
                    {"verb": "activate", "target": "Save"},
                ],
            },
        ],
    )


def test_synthetic_graph_reads_unsupplied_mail_quantity_then_fills_and_saves_sheet(tmp_path):
    """Existing fake model/window relay exercise the real graph; no live apps or calls."""
    from sawon_interfaces import ActionResult
    from test_window_actions import setup_window_brain

    from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES

    quantity = "27"
    reference = "collected_result.quantity"
    mail = reading(["Mail", "Synthetic order"], {"quantity": quantity}).model_dump()
    mail.update(
        screen_kind="query",
        steps=[{"action": "focus_window", "handle": 2, "intent": "navigate"}],
        observed_step_results=[
            {
                "step_id": "read-mail",
                "observed_result": "Read the order quantity",
                "completed": True,
            }
        ],
    )
    sheet = {
        "screen_kind": "editor",
        "screen_path": ["Calc", "Synthetic ledger"],
        "fields": {
            "Quantity cell": {
                "role": "cell",
                "bbox": [10, 10, 100, 20],
                "source": reference,
                "value": "",
            },
            "Save": {"role": "button", "bbox": [130, 10, 80, 20]},
        },
        "steps": [
            {
                "action": "fill",
                "target": "Quantity cell",
                "point": [40, 20],
                "intent": "fill",
                "source": reference,
            }
        ],
        "taught_step_id": "update-sheet",
    }
    filled = {
        **sheet,
        "fields": {
            **sheet["fields"],
            "Quantity cell": {**sheet["fields"]["Quantity cell"], "value": quantity},
        },
        "steps": [{"action": "click", "target": "Save", "point": [160, 20], "intent": "save"}],
    }
    completed = {
        **filled,
        "operation": "complete",
        "steps": [],
        "observed_step_results": [
            {"step_id": "update-sheet", "observed_result": "Spreadsheet saved", "completed": True}
        ],
    }
    record = TaskInputRecord(case_id="synthetic-mail-to-sheet", inputs={})
    brain, model, relay, switched = setup_window_brain(
        tmp_path,
        record,
        [mail, sheet, filled, completed],
        task_instruction=synthetic_quantity_transfer_task(),
    )
    focus = relay.act
    actions = []
    saved = []

    def act(action):
        # Only this fake relay stores the synthetic spreadsheet; never an app API.
        item = action.root
        if item.type == "focus_window":
            return focus(action)
        actions.append(item)
        if item.type == "click":
            saved.append(actions[0].text)
        return ActionResult(
            action_id=item.action_id,
            ok=True,
            post_obs_id=relay.observe().obs_id,
            duration_ms=1,
        )

    relay.act = act

    outcome = brain.run()

    assert outcome.status == "success", [event.payload for event in brain.log.read_all()[-12:]]
    assert switched == [2]
    assert [action.type for action in actions] == ["set_text", "click"]
    assert actions[0].text == quantity, (
        "Relay text must come from the email reading, not input data"
    )
    assert saved == [quantity]
    assert brain.values == record.inputs == {}
    assert brain.input_values == {reference: quantity}
    contexts = [json.loads(prompt[len(CONTEXT_MAPPING_RULES) :]) for prompt in model.prompts]
    assert len(contexts) == 4
    assert contexts[0]["notebook"]["collected_result"]["values"] == {}
    assert all(
        context["notebook"]["collected_result"]["values"] == {"quantity": quantity}
        for context in contexts[1:]
    ), "The empty destination must not erase the source reading on the next request"
    result = collected_result(brain.screen_nodes, ["quantity"])
    assert result["values"] == {"quantity": quantity}
    assert [item["screen_path"] for item in result["provenance"]["quantity"]] == [
        ["Mail", "Synthetic order"]
    ]
    destination = brain.screen_nodes['["Calc", "Synthetic ledger"]']["fields"][reference]
    assert destination["delivery"]["value"] == quantity
    assert destination["last_observation"]["value"] == quantity


def test_synthetic_observed_input_view_tracks_source_update_and_explicit_blank(tmp_path):
    """Existing fake memo writes prove values are resolved anew, not frozen after collection."""
    from test_window_actions import setup_window_brain

    record = TaskInputRecord(case_id="synthetic-observed-input-updates", inputs={})
    brain, _, relay, _ = setup_window_brain(
        tmp_path, record, [], task_instruction=synthetic_quantity_transfer_task()
    )
    reference = "collected_result.quantity"
    assert brain.input_values == {}
    brain.observation = relay.observe()
    brain.observation_number = 1
    brain._remember_model_task_evidence(
        SimpleNamespace(
            mapping=reading(["Calc", "Synthetic ledger"], {reference: ""}),
            observed_step_results=[],
        )
    )
    assert reference in brain.screen_nodes['["Calc", "Synthetic ledger"]']["fields"]
    assert brain.input_values == {}, "An empty destination never invents the unavailable mail value"
    for number, quantity in enumerate(("27", "31", ""), start=2):
        brain.observation = relay.observe()
        brain.observation_number = number
        brain._remember_model_task_evidence(
            SimpleNamespace(
                mapping=reading(["Mail", "Synthetic order"], {"quantity": quantity}),
                observed_step_results=[],
            )
        )
        assert brain.input_values == ({reference: quantity} if quantity else {}), quantity
        assert brain.values == record.inputs == {}


def synthetic_observed_quantity_fill(quantity=None):
    """A synthetic composite screen exposes a source reading and destination cell together."""
    fields = {
        "Quantity cell": {
            "role": "cell",
            "bbox": [10, 50, 100, 20],
            "source": "collected_result.quantity",
            "value": "",
        }
    }
    if quantity is not None:
        fields["Mail quantity"] = {
            "role": "text",
            "bbox": [10, 10, 100, 20],
            "source": "quantity",
            "value": quantity,
        }
    return {
        "screen_kind": "editor",
        "screen_path": ["Synthetic combined view"],
        "fields": fields,
        "steps": [
            {
                "action": "fill",
                "target": "Quantity cell",
                "point": [40, 60],
                "intent": "fill",
                "source": "collected_result.quantity",
            }
        ],
        "taught_step_id": "update-sheet",
    }


def test_synthetic_graph_read_and_fill_in_same_response_resolves_new_reading(tmp_path):
    """Existing fake model/relay isolate read→prepare→wire; the next response stops the test."""
    from sawon_interfaces import ActionResult
    from test_window_actions import setup_window_brain

    quantity = "43"
    fill = synthetic_observed_quantity_fill(quantity)
    stop = {**fill, "operation": "stop", "steps": []}
    record = TaskInputRecord(
        case_id="synthetic-same-response-source", inputs={"query.email": "Synthetic order"}
    )
    brain, model, relay, _ = setup_window_brain(
        tmp_path, record, [fill, stop], task_instruction=synthetic_quantity_transfer_task()
    )
    sent = []

    def act(action):
        sent.append(action.root)
        return ActionResult(
            action_id=action.root.action_id,
            ok=True,
            post_obs_id=relay.observe().obs_id,
            duration_ms=1,
        )

    relay.act = act

    outcome = brain.run()

    assert outcome.reason == "model_requested_stop"
    assert len(model.prompts) == 2, (
        "Reading and writing must not require an intermediate model turn"
    )
    assert [action.type for action in sent] == ["set_text"]
    assert sent[0].text == quantity
    assert brain.values == record.inputs == {"query.email": "Synthetic order"}
    assert brain.input_values == {**record.inputs, "collected_result.quantity": quantity}
    assert not any(event.payload.get("guard") == "input_scope" for event in brain.log.read_all()), (
        "The new reading must be available during plan validation, not only during execution"
    )
    destination = brain.screen_nodes['["Synthetic combined view"]']["fields"][
        "collected_result.quantity"
    ]
    assert destination["delivery"]["value"] == quantity


def test_synthetic_graph_missing_source_reading_never_dispatches_empty_text(tmp_path):
    """A fake model proposes an unresolved source; no live app or source answer is provided."""
    from test_window_actions import setup_window_brain

    fill = synthetic_observed_quantity_fill()
    stop = {**fill, "operation": "stop", "steps": []}
    record = TaskInputRecord(case_id="synthetic-missing-source", inputs={})
    brain, model, relay, _ = setup_window_brain(
        tmp_path, record, [fill, stop], task_instruction=synthetic_quantity_transfer_task()
    )
    sent = []
    relay.act = lambda action: sent.append(action)

    outcome = brain.run()

    assert outcome.reason == "model_requested_stop"
    assert len(model.prompts) == 2, "The unresolved fill must be evaluated before the stop response"
    assert sent == [], "Unavailable observations must not become an empty-string write"
    assert brain.input_values == brain.values == {}
    assert collected_result(brain.screen_nodes, ["quantity"])["missing_keys"] == ["quantity"]
