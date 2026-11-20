"""A synthetic shipment and quote use the same mail UI with different education and facts."""

import json
from hashlib import sha256
from itertools import repeat

from sawon_interfaces import ActionResult, AvailableApp, WindowInfo
from test_screen_decision import matching_contract, setup_brain
from test_visual_scene import wire

from brain.cache.methods import MethodDocument
from brain.demo_learning.memory_compilation import MemoryCandidate, compile_memory
from brain.graph.screen_decision import build_decision_prompt
from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES
from brain.graph.screen_memory import collected_result
from brain.task_instruction import TaskInputRecord, TaskInstruction
from pipeline.schemas import OcrBlock
from sawon_runtime.storage import atomic_write_json


def mail_screen(title, lines):
    window = {"handle": 21, "title": title, "process": "chrome.exe"}
    return {
        "active_window": window,
        "windows": [window],
        "available_apps": [{"id": "gmail", "name": "Gmail"}],
        "image_size": [400, 200],
        "screen_phash": 42,
        "markers": [],
        "ocr": [
            {"id": i, "text": line, "bbox": [12, 25 + i * 45, 200, 20]}
            for i, line in enumerate(lines)
        ],
    }


def compile_shipment_mail():
    task = TaskInstruction(
        name="Shipment intake",
        objective="Locate the shipment notice and register its shipper in ERP.",
        completion_conditions=["The shipment is registered."],
        result_keys=["shipper"],
        procedure=[
            {
                "id": "read-shipment",
                "instruction": "Search Gmail for the notice, open it and read the shipper.",
                "completion": "The shipper is known.",
                "actions": [
                    {
                        "verb": "fill",
                        "target": "Mail search for the WEST-DOCK-ALPHA team",
                        "input_key": "shipment_query",
                    }
                ],
            }
        ],
    )
    source = {
        "source_case": "synthetic-shipment",
        "task": task.model_dump(mode="json"),
        "task_sha256": sha256(task.model_dump_json().encode()).hexdigest(),
        "values": {"shipment_query": "SHIP-A07", "collected_result.shipper": "SENDER-OLD"},
    }
    plans = [
        (
            ["Mail search"],
            {
                "Mail search": {
                    "role": "edit",
                    "bbox": [12, 25, 200, 20],
                    "source": "shipment_query",
                }
            },
            {
                "action": "fill",
                "intent": "fill",
                "target": "Mail search",
                "source": "shipment_query",
                "point": [80, 32],
            },
        ),
        (
            ["SHIP-A07 from SENDER-OLD"],
            {"SHIP-A07 from SENDER-OLD": {"role": "row", "bbox": [12, 25, 200, 20]}},
            {
                "action": "click",
                "intent": "navigate",
                "target": "SHIP-A07 from SENDER-OLD",
                "point": [120, 30],
            },
        ),
        (
            ["Shipper: SENDER-OLD"],
            {
                "Shipper": {
                    "role": "text",
                    "bbox": [12, 25, 200, 20],
                    "source": "shipper",
                    "value": "SENDER-OLD",
                }
            },
            {"action": "click", "intent": "navigate", "target": "Message body", "point": [55, 32]},
        ),
    ]
    candidates = []
    for index, (lines, fields, step) in enumerate(plans):
        mapping = {
            "screen_kind": "editor" if index == 2 else "query",
            "screen_path": ["Gmail"],
            "taught_step_id": "read-shipment",
            "fields": fields,
            "steps": [step],
        }
        candidates.append(
            MemoryCandidate(
                f"c{index:03}",
                "a" * 64,
                f"obs-{index}",
                index,
                mail_screen("Inbox - Gmail - Chrome", lines),
                mapping,
            )
        )
    draft = {
        "purpose": task.objective,
        "groups": [
            {
                "candidate_id": c.candidate_id,
                "purpose": "Read shipment",
                "expected_result": "Locate the sender",
            }
            for c in candidates
        ],
    }
    return compile_memory(source, candidates, draft, method_id="shipment-only"), task


def test_synthetic_shipment_mail_guide_reaches_quote_decisions_and_new_notebook(tmp_path):
    # Fake provider and desktop. The worker runs its actual graph and recorded app guide.
    method, previous_task = compile_shipment_mail()
    task = TaskInstruction(
        name="Quotation",
        objective="Find the requested quotation and read its price and reply deadline.",
        requirements=["Use the quote phrase to identify the current mail."],
        completion_conditions=["The price is known.", "The reply deadline is known."],
        result_keys=["price", "deadline"],
    )
    record = TaskInputRecord(case_id="synthetic-quote", inputs={"quote_phrase": "RFQ-B82"})
    brain, model, relay, ocr, detector, _ = setup_brain(tmp_path, record, [], task_instruction=task)
    brain.goal = task.objective
    atomic_write_json(brain.method_library.path, MethodDocument(methods=[method]))
    brain.method_library.enabled = True
    assert brain.task_instruction_sha256 != method.task_contract_sha256
    assert brain.task_instruction.objective != previous_task.objective

    screens = [
        mail_screen("Inbox - Gmail - Chrome", ["Mail search"]),
        mail_screen("Results - Gmail - Chrome", ["Another quotation", "RFQ-B82"]),
        mail_screen("RFQ-B82 - Gmail - Chrome", ["Price: USD 125", "Deadline: Friday"]),
    ]
    actions = []

    def observe():
        frame = screens[len(actions)]
        window = WindowInfo(**frame["active_window"])
        return wire().model_copy(
            update={
                "active_window": window,
                "windows": [window],
                "available_apps": [AvailableApp(**app) for app in frame["available_apps"]],
            }
        )

    def deliver(action):
        actions.append(action.root)
        relay.color = ("lightgrey", "black")[len(actions) - 1]
        return ActionResult(action_id=action.root.action_id, ok=True, duration_ms=1)

    def decide(prompt):
        ctx = json.loads(prompt.removeprefix(CONTEXT_MAPPING_RULES))
        (guide,) = ctx["method_memory"]["app_guides"]
        assert guide["app_id"] == "gmail" and ctx["method_memory"]["methods"] == []
        for old in (
            "shipment-only",
            "SHIP-A07",
            "SENDER-OLD",
            "shipment_query",
            "shipper",
            "WEST-DOCK-ALPHA",
        ):
            assert old not in json.dumps(ctx), old
        regions = ctx["screen"]["text_regions"]
        data = {"screen_path": ["Gmail"], "screen_kind": "query", "fields": [], "steps": []}
        if len(actions) < 2:
            # Make the fake provider consume A's gesture, but B's input and B's current OCR.
            gesture = guide["steps"][len(actions)]["action"]
            text = "Mail search" if gesture == "fill" else ctx["notebook"]["inputs"]["quote_phrase"]
            region = next(r for r in regions if r["text"] == text)
            x, y, w, h = region["bbox"]
            step = {
                "action": gesture,
                "intent": "fill" if gesture == "fill" else "navigate",
                "target": text,
                "point": [x + w / 2, y + h / 2],
            }
            if gesture == "fill":
                step["source"] = "quote_phrase"
                data["fields"].append(
                    {
                        "name": text,
                        "role": "edit",
                        "source": "quote_phrase",
                        "region_refs": [region["ref"]],
                    }
                )
            data["steps"].append(step)
            return data
        required = ctx["notebook"]["collected_result"]["missing_keys"]
        for region in regions:
            key, value = region["text"].split(": ")
            if key.lower() not in required:
                continue
            data["fields"].append(
                {
                    "name": key,
                    "role": "text",
                    "source": key.lower(),
                    "region_refs": [region["ref"]],
                    "value": value,
                }
            )
        data.update(
            screen_kind="editor",
            operation="complete",
            completion_evidence=[
                {"condition_index": i, "target": f["name"]} for i, f in enumerate(data["fields"])
            ],
        )
        return data

    model.decisions = repeat(decide)
    relay.health = lambda: True
    relay.observe, relay.act = observe, deliver
    detector.read = lambda *_args, **_kwargs: []
    ocr.read = lambda _page: [
        OcrBlock(**block, confidence=1) for block in screens[len(actions)]["ocr"]
    ]
    outcome = brain.run()

    assert outcome.status == "success", outcome
    assert [a.type for a in actions] == ["set_text", "click"]
    assert actions[0].text == "RFQ-B82"
    assert actions[1].target.image_point == (112, 80)
    result = collected_result(brain.screen_nodes, task.result_keys)
    assert result["values"] == {"price": "USD 125", "deadline": "Friday"}
    assert {
        item["observation_id"] for facts in result["provenance"].values() for item in facts
    } == {str(brain.observation.obs_id)}
    assert not any(e.payload.get("check") == "method_bound" for e in brain.log.read_all())

    other_tab = mail_screen("Gmail budget - Spreadsheet - Chrome", ["RFQ-B82"])
    request, services = matching_contract(brain, other_tab)
    assert not build_decision_prompt(request, services).state["method_memory"].get("app_guides")
