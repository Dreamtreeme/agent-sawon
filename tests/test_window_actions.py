"""Synthetic window switching through the existing fake model and desktop contracts."""

import json
from copy import deepcopy

import pytest
from PIL import Image
from sawon_interfaces import ActionResult, AvailableApp, WindowInfo
from test_anthropic_client import setup as setup_anthropic
from test_brain import record as record
from test_memory_retrieval import setup as setup_memory
from test_screen_decision import setup_brain
from test_visual_markers import fixture
from test_visual_scene import wire

from brain.graph.action_preparation import ActionGuardRequest, validate_action_guard
from brain.graph.execution import GuardStop
from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.graph.schemas import Choice, Decision
from brain.graph.screen_decision import ScreenMatch, compile_screen_action_plan
from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES, ScreenMapping
from brain.visual.frame import frame_input
from brain.visual.scanning import ScreenScanner
from models.schema import NullableWireCodec
from sawon_runtime.storage import RunLog


def focus_response(handle):
    return {
        "screen_kind": "menu",
        "screen_path": ["Synthetic app"],
        "fields": {},
        "steps": [{"action": "focus_window", "handle": handle, "intent": "navigate"}],
        "purpose": "Read the other app",
        "expected_result": "The selected window is visible",
    }


def setup_window_brain(tmp_path, record, responses, *, task_instruction=None, registered_app=None):
    """Reuse the existing fake model/scanner with a synthetic two-window desktop."""
    brain, model, relay, *_ = setup_brain(
        tmp_path, record, responses, task_instruction=task_instruction
    )
    windows = [
        WindowInfo(handle=1, title="Synthetic mail", process="mail.exe"),
        WindowInfo(handle=2, title="Synthetic chat", process="chat.exe"),
    ]
    closed_window = windows.pop() if registered_app is not None else None
    active = [windows[0]]
    sent = []

    def observe():
        relay.current = wire().model_copy(
            update={
                "active_window": active[0],
                "windows": list(windows),
                "available_apps": [registered_app] if registered_app is not None else [],
            }
        )
        return relay.current

    def act(action):
        payload = action.model_dump(mode="json")
        assert "target" not in payload
        if payload["type"] == "open_app":
            assert payload["app_id"] == registered_app.id
            sent.append(payload["app_id"])
            if closed_window not in windows:
                windows.append(closed_window)
            active[0] = closed_window
        else:
            assert payload["type"] == "focus_window"
            sent.append(payload["handle"])
            active[0] = next(window for window in windows if window.handle == payload["handle"])
        return ActionResult(
            action_id=action.root.action_id,
            ok=True,
            post_obs_id=observe().obs_id,
            duration_ms=1,
        )

    relay.observe, relay.act = observe, act
    relay.health = lambda: {"observation_mode": "visual_only", "observation_only": False}
    return brain, model, relay, sent


@pytest.mark.parametrize("existing_method", [False, True], ids=["new-task", "memory-fallback"])
def test_synthetic_focus_switches_apps_without_geometry_or_extra_model_calls(
    tmp_path, record, existing_method
):
    """The existing fake model/relay exercise A→B→A; no real desktop is controlled."""
    responses = [
        focus_response(2),
        focus_response(1),
        {**focus_response(1), "operation": "stop", "steps": []},
    ]
    if existing_method:
        # Synthetic existing-method fallback using the same fake scanner and window relay.
        brain, model, relay = setup_memory(tmp_path / "memory")
        _, _, window_relay, sent = setup_window_brain(
            tmp_path / "windows", brain.record, responses, task_instruction=brain.task_instruction
        )
        relay.observe, relay.act, relay.health = (
            window_relay.observe,
            window_relay.act,
            window_relay.health,
        )
        model.decisions = iter(responses)
    else:
        brain, model, relay, sent = setup_window_brain(tmp_path, record, responses)
    for expected in (2, 1):
        brain._observe()
        route = brain._select_node()
        assert route == "review", [event.payload for event in brain.log.read_all()[-5:]]
        assert brain.decision.action.handle == expected
        assert brain.decision.action.ref is None
        assert brain.visual_match_candidate is None
        assert brain._review_node() == "execution"
        assert brain._act_node() == "observation", brain.result
        assert not brain.memory_retrieval.state.used, (
            "A window switch did not perform method inputs"
        )
        assert not brain.memory_retrieval.state.history
        assert not brain.memory_retrieval.state.staged
    brain._observe()

    assert sent == [2, 1]
    assert len(model.prompts) == 3
    contexts = [json.loads(prompt[len(CONTEXT_MAPPING_RULES) :]) for prompt in model.prompts]
    assert [context["screen"]["active_window"]["handle"] for context in contexts] == [1, 2, 1]
    assert all(
        [window["handle"] for window in context["screen"]["windows"]] == [1, 2]
        for context in contexts
    )
    assert not any(
        event.payload.get("purpose") == "input_evidence_recovery" for event in brain.log.read_all()
    )


def test_synthetic_registered_app_open_reaches_relay_and_reobserves_new_window(tmp_path, record):
    """Only the fake relay opens the synthetic closed app; no real app is launched."""
    response = {
        **focus_response(2),
        "steps": [{"action": "open_app", "app_id": "synthetic-chat", "intent": "navigate"}],
    }
    brain, model, relay, sent = setup_window_brain(
        tmp_path,
        record,
        [response, {**focus_response(2), "operation": "stop", "steps": []}],
        registered_app=AvailableApp(id="synthetic-chat", name="Synthetic chat"),
    )

    outcome = brain.run()

    assert outcome.reason == "model_requested_stop"
    assert sent == ["synthetic-chat"]
    assert relay.current.active_window.handle == 2
    events = brain.log.read_all()
    requests = [
        event.payload["action"]
        for event in events
        if event.kind == "act" and event.payload.get("stage") == "requested"
    ]
    assert len(requests) == 1
    assert requests[0]["type"] == "open_app" and requests[0]["app_id"] == "synthetic-chat"
    assert not {"target", "path", "url", "command"} & requests[0].keys()
    receipts = [
        event.payload
        for event in events
        if event.kind == "act" and event.payload.get("stage") == "returned"
    ]
    assert len(receipts) == 1 and receipts[0]["ok"] is True
    assert receipts[0]["post_obs_id"] is not None
    contexts = [json.loads(prompt[len(CONTEXT_MAPPING_RULES) :]) for prompt in model.prompts]
    assert len(contexts) == 2
    assert contexts[0]["screen"]["available_apps"] == [
        {"id": "synthetic-chat", "name": "Synthetic chat"}
    ]
    assert [context["screen"]["active_window"]["handle"] for context in contexts] == [1, 2]


def test_synthetic_unregistered_app_is_rejected_before_dispatch():
    decision = Decision(
        screen_kind="menu",
        operation="act",
        intent="navigate",
        subgoal="Open a registered app",
        reason="Synthetic unknown ID",
        action=Choice(type="open_app", app_id="not-registered"),
    )
    with pytest.raises(GuardStop, match="application_not_registered"):
        validate_action_guard(
            ActionGuardRequest(
                observation=wire(),
                decision=decision,
                policy=DecisionPolicy(ExecutionFacts(phase="task")),
                phase="task",
            )
        )


def test_synthetic_window_actions_cannot_batch_coordinates_before_reobservation():
    for step in (
        focus_response(2)["steps"][0],
        {"action": "open_app", "app_id": "synthetic-chat", "intent": "navigate"},
    ):
        mapping = ScreenMapping.model_validate(
            {
                **focus_response(2),
                "fields": {"Button": {"role": "button", "bbox": [10, 20, 80, 25]}},
                "steps": [
                    step,
                    {
                        "action": "click",
                        "target": "Button",
                        "point": [20, 30],
                        "intent": "navigate",
                    },
                ],
            }
        )
        response = ScreenMatch.model_validate(mapping.local_payload({}))
        with pytest.raises(ValueError, match="window_action_requires_observation_boundary"):
            compile_screen_action_plan(response, None)


def test_synthetic_window_context_survives_scanning_and_rebuilt_planning_context(tmp_path):
    blocks, boxes, _, meaning = fixture()
    windows = [
        WindowInfo(handle=1, title="Synthetic A", process="a.exe"),
        WindowInfo(handle=2, title="Synthetic B", process="b.exe"),
    ]
    requests = []

    class Relay:
        def screenshot(self, observation, path):
            path.parent.mkdir(parents=True)
            Image.new("RGB", (400, 200), "white").save(path)
            return path

    class Ocr:
        def read(self, page):
            return blocks

    class Detector:
        def read(self, page):
            return boxes

    def interpret(request):
        requests.append(frame_input(request.screen))
        return meaning

    scanner = ScreenScanner(
        Relay(),
        Ocr(),
        tmp_path,
        RunLog(tmp_path, case_id="windows", agent_id="test"),
        interpret,
        detector=Detector(),
        model_led_semantics=True,
    )
    for active in windows:
        scanner.scan(
            wire().model_copy(
                update={
                    "active_window": active,
                    "windows": windows,
                    "available_apps": [AvailableApp(id="synthetic-app", name="Synthetic app")],
                }
            )
        )
        context = frame_input(scanner.planning_context()[0].screen)
        assert context["active_window"] == active.model_dump(mode="json")
        assert context["windows"] == [window.model_dump(mode="json") for window in windows]
        assert context["available_apps"] == [{"id": "synthetic-app", "name": "Synthetic app"}]
    assert [request["active_window"]["handle"] for request in requests] == [1, 2]
    requests[0]["windows"].clear()
    assert len(scanner.planning_context()[0].screen["windows"]) == 2


def test_synthetic_focus_flat_wire_sentinels_never_become_executor_coordinates(tmp_path):
    client, sdk, log = setup_anthropic(tmp_path)
    codec = NullableWireCodec(
        ScreenMatch.model_json_schema(), ScreenMatch.anthropic_nulls, ScreenMatch.anthropic_unions
    )
    assert "minItems" not in codec.schema["properties"]["steps"]["items"]["properties"]["point"]
    for raw in (
        focus_response(2),
        {
            **focus_response(2),
            "steps": [{"action": "open_app", "app_id": "synthetic-chat", "intent": "navigate"}],
        },
    ):
        encoded = codec.convert(raw, encode=True)
        assert encoded["steps"][0]["target"] == "", raw["steps"]
        assert encoded["steps"][0]["point"] == [], raw["steps"]
        assert codec.convert(encoded)["steps"] == raw["steps"]
        sdk.messages.create.return_value.content[0].text = json.dumps(encoded)
        response = client.complete_json("Synthetic window action", ScreenMatch).response
        assert response.mapping.steps[0].model_dump() == raw["steps"][0]

        invalid = deepcopy(encoded)
        invalid["steps"][0]["point"] = [0, 0]
        sdk.messages.create.return_value.content[0].text = json.dumps(invalid)
        with pytest.raises(ValueError, match="point"):
            client.complete_json("Synthetic window action cannot carry a point", ScreenMatch)
        assert log.read_all()[-1].cost.usd is not None

    click = {
        **focus_response(2),
        "fields": {"Button": {"role": "button", "bbox": [10, 20, 80, 25]}},
        "steps": [{"action": "click", "target": "Button", "point": [], "intent": "navigate"}],
    }
    sdk.messages.create.return_value.content[0].text = json.dumps(codec.convert(click, encode=True))
    with pytest.raises(ValueError, match="point"):
        client.complete_json("Synthetic click still requires a real point", ScreenMatch)
    assert log.read_all()[-1].cost.usd is not None
