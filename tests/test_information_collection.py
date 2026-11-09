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


def test_synthetic_graph_collects_mail_and_chat_registers_then_requeries_erp(tmp_path):
    """Synthetic existing fake model/scanner/window relay run the real graph, without GUI/API.

    The fake ERP saves only received wire text; the final model reading is populated
    from that saved text. Business answers exist solely in the synthetic source replies.
    """
    from sawon_interfaces import ActionResult, WindowInfo
    from test_window_actions import setup_window_brain

    from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES

    mail_values = {
        "hbl_no": "SYNTHETIC-HBL-83",
        "mbl_no": "SYNTHETIC-MBL-19",
        "shipper": "SYNTHETIC EXPORTER",
        "consignee": "SYNTHETIC IMPORTER",
        "description": "Synthetic replacement parts",
    }
    chat_values = {
        "packages.count": "26",
        "packages.unit": "CTNS",
        "gross_weight_kg": "292",
        "container_no": "SYNTHETIC-CONTAINER-41",
        "pol": "SYNTHETIC-LOAD",
        "pod": "SYNTHETIC-DISCHARGE",
    }
    expected = {**mail_values, **chat_values}
    refs = {source: "collected_result." + source for source in expected}
    task = TaskInstruction(
        name="Synthetic source apps to existing ERP",
        objective="Read mail and chat, register the shipment, save, then requery its HBL.",
        identity_input_key=refs["hbl_no"],
        result_keys=list(expected),
        completion_conditions=["The newly saved shipment has been requeried by its read HBL."],
        procedure=[
            {
                "id": "collect-mail",
                "instruction": "Read the shipment identifiers and parties in the specified mail.",
                "completion": "Shipment mail facts have been read.",
            },
            {
                "id": "collect-chat",
                "instruction": "Read the remaining shipment information in the specified chat.",
                "completion": "The chat facts are associated with the same shipment.",
            },
            {
                "id": "register",
                "instruction": "Query the read HBL and create a shipment only if it is absent.",
                "completion": "All collected shipment fields have been saved once.",
                "actions": [
                    {"verb": "fill", "target": source, "input_key": reference}
                    for source, reference in refs.items()
                ],
            },
            {
                "id": "requery",
                "instruction": "Search the saved shipment by the same read HBL and open its row.",
                "completion": "The saved shipment detail is visible after a fresh search.",
                "actions": [{"verb": "fill", "target": "Query HBL", "input_key": refs["hbl_no"]}],
            },
        ],
    )
    mail = reading(["Mail", "Synthetic shipment"], mail_values).model_dump()
    mail.update(
        screen_kind="query",
        steps=[{"action": "focus_window", "handle": 2, "intent": "navigate"}],
        taught_step_id="collect-chat",
    )
    chat = reading(["Chat", "Synthetic shipment"], chat_values).model_dump()
    chat.update(
        screen_kind="query",
        steps=[{"action": "focus_window", "handle": 3, "intent": "navigate"}],
        taught_step_id="register",
    )
    query_fields = {
        "Query HBL": {
            "role": "edit",
            "bbox": [220, 42, 130, 16],
            "source": refs["hbl_no"],
            "value": "",
        },
        "Search": {"role": "button", "bbox": [220, 72, 80, 16]},
        "New": {"role": "button", "bbox": [220, 102, 80, 16]},
    }
    query_steps = [
        {
            "action": "fill",
            "target": "Query HBL",
            "point": [250, 50],
            "intent": "fill",
            "source": refs["hbl_no"],
        },
        {"action": "click", "target": "Search", "point": [250, 80], "intent": "search"},
    ]
    query = {
        "screen_kind": "query",
        "screen_path": ["ERP", "HBL query"],
        "fields": query_fields,
        "steps": query_steps,
        "taught_step_id": "register",
    }
    new = {
        **query,
        "fields": {
            **query_fields,
            "Query HBL": {**query_fields["Query HBL"], "value": expected["hbl_no"]},
        },
        "steps": [{"action": "click", "target": "New", "point": [250, 110], "intent": "navigate"}],
    }
    editor_fields = {
        source: {
            "role": "edit",
            "bbox": [10, 10 + index * 14, 160, 12],
            "source": refs[source],
            "value": "",
        }
        for index, source in enumerate(expected)
    }
    editor = {
        "screen_kind": "editor",
        "screen_path": ["ERP", "Shipment editor"],
        "fields": {**editor_fields, "Save": {"role": "button", "bbox": [220, 12, 80, 16]}},
        "steps": [
            {
                "action": "fill",
                "target": source,
                "point": [70, 16 + index * 14],
                "intent": "fill",
                "source": refs[source],
            }
            for index, source in enumerate(expected)
        ]
        + [{"action": "click", "target": "Save", "point": [250, 20], "intent": "save"}],
        "taught_step_id": "register",
    }
    requery = {**query, "taught_step_id": "requery"}
    open_saved = {
        **query,
        "fields": {
            **new["fields"],
            "Saved shipment": {"role": "dataitem", "bbox": [220, 132, 130, 16]},
        },
        "steps": [
            {
                "action": "click",
                "target": "Saved shipment",
                "point": [250, 140],
                "intent": "navigate",
            }
        ],
        "taught_step_id": "requery",
    }
    completed = {
        **editor,
        "screen_path": ["ERP", "Requeried shipment"],
        "fields": {source: dict(field) for source, field in editor_fields.items()},
        "steps": [],
        "operation": "complete",
        "taught_step_id": "requery",
    }
    record = TaskInputRecord(case_id="synthetic-mail-chat-erp", inputs={})
    brain, model, relay, switched = setup_window_brain(
        tmp_path,
        record,
        [mail, chat, query, new, editor, requery, open_saved, completed],
        task_instruction=task,
    )
    base_observe, base_act = relay.observe, relay.act
    erp_window = WindowInfo(handle=3, title="Synthetic ERP", process="erp.exe")
    erp_active = False
    entered, saved, searches = {}, {}, []
    transmitted = []

    def observe():
        wire = base_observe()
        relay.current = wire.model_copy(
            update={
                "windows": [*wire.windows, erp_window],
                "active_window": erp_window if erp_active else wire.active_window,
            }
        )
        return relay.current

    def act(action):
        nonlocal erp_active
        item = action.root
        transmitted.append(item)
        if item.type == "focus_window":
            erp_active = item.handle == 3
            if not erp_active:
                return base_act(action)
            switched.append(3)
        else:
            assert erp_active, "All shipment writes must target the fake ERP window"
            point = list(item.target.image_point)
            if item.type == "set_text":
                if point == [250, 50]:
                    entered["query"] = item.text
                else:
                    source = list(expected)[int((point[1] - 16) / 14)]
                    entered[source] = item.text
            elif point == [250, 80]:
                searches.append(entered["query"])
            elif point == [250, 20]:
                saved.update({source: entered[source] for source in expected})
                for source, field in completed["fields"].items():
                    field["value"] = saved[source]
            elif point == [250, 140]:
                assert searches[-1] == saved["hbl_no"], "Detail must follow the saved HBL query"
            else:
                assert point == [250, 110], point
        return ActionResult(
            action_id=item.action_id, ok=True, post_obs_id=observe().obs_id, duration_ms=1
        )

    relay.observe, relay.act = observe, act

    outcome = brain.run()

    assert outcome.status == "success", [event.payload for event in brain.log.read_all()[-12:]]
    assert switched == [2, 3]
    assert searches == [expected["hbl_no"], expected["hbl_no"]]
    assert saved == expected, "Saved fields must come from received wire text, not fixture answers"
    assert len([action for action in transmitted if action.type == "set_text"]) == len(expected) + 2
    assert brain.values == record.inputs == {}
    assert brain.identity_input_key == refs["hbl_no"]
    contexts = [json.loads(prompt[len(CONTEXT_MAPPING_RULES) :]) for prompt in model.prompts]
    assert contexts[0]["notebook"]["inputs"] == {}
    assert contexts[1]["notebook"]["collected_result"]["values"] == mail_values
    assert contexts[2]["notebook"]["collected_result"]["values"] == expected
    assert contexts[-1]["notebook"]["collected_result"]["values"] == expected
    result = collected_result(brain.screen_nodes, list(expected))
    assert result["values"] == expected
    assert result["provenance"]["hbl_no"][0]["screen_path"] == ["Mail", "Synthetic shipment"]
    assert result["provenance"]["packages.count"][0]["screen_path"] == [
        "Chat",
        "Synthetic shipment",
    ]


@pytest.mark.parametrize("identity", ["missing", "collected_result.not_declared"])
def test_missing_undeclared_identity_still_rejects_before_collection(tmp_path, identity):
    """Both a static typo and an undeclared collected reference remain invalid configuration."""
    from test_window_actions import setup_window_brain

    task = synthetic_quantity_transfer_task().model_copy(update={"identity_input_key": identity})
    record = TaskInputRecord(case_id="synthetic-invalid-identity", inputs={})
    with pytest.raises(ValueError, match="task_identity_input_missing"):
        setup_window_brain(tmp_path, record, [], task_instruction=task)


def test_synthetic_collected_shipment_sources_preserve_numeric_and_unit_comparison():
    """The same shipment facts retain their existing comparison meaning after source binding."""
    from brain.observe.values import field_matches

    original = {"packages.count": "24", "packages.unit": "CTNS", "gross_weight_kg": "288"}
    references = {"collected_result." + source: value for source, value in original.items()}
    for source, actual, expected in [
        ("gross_weight_kg", "288.000", True),
        ("packages.count", "24 CTNS", True),
        ("packages.unit", "24 CTNS", True),
        ("gross_weight_kg", "289.000", False),
        ("packages.count", "25 CTNS", False),
        ("packages.unit", "24 PCS", False),
    ]:
        label = f"{source} read as {actual!r}"
        assert field_matches(source, actual, original) is expected, label
        assert field_matches("collected_result." + source, actual, references) is expected, label
