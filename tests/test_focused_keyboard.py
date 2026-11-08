"""Synthetic reproduction of a live fill→untargeted Enter response (2026-11-08)."""

import json
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError
from sawon_interfaces import ActionBatchResult, ActionResult
from test_anthropic_client import setup as setup_anthropic
from test_screen_decision import setup_brain
from test_visual_scene import wire

from brain.cache.methods import method_from_match
from brain.graph.screen_decision import MatchAction
from brain.graph.screen_mapping import ScreenMapping
from brain.task_instruction import TaskInputRecord, TaskInstruction
from models.anthropic_client import AnthropicClient
from models.pricing import PriceBook


def replay_focused_key_response(tmp_path, raw, *, image_size=(400, 200)):
    """Replay a response through the real adapter/worker with only SDK and relay faked."""
    record = TaskInputRecord(
        case_id="synthetic-focused-key", inputs={"query.email": "synthetic order"}
    )
    task = TaskInstruction.model_validate_json(Path("examples/chat-email-task.json").read_bytes())
    brain, _, relay, *_ = setup_brain(tmp_path, record, [], task_instruction=task)
    _, sdk, _ = setup_anthropic(tmp_path / "fake-provider")
    sdk.messages.create.return_value.content[0].text = raw
    brain.model = AnthropicClient(
        sdk_client=sdk,
        model="claude-haiku-5-5",
        price=PriceBook.load(Path("models/pricing.toml")).for_model("claude-haiku-5-5"),
        run_log=brain.log,
    )
    brain.memory_retrieval.enabled = brain.memory_retrieval.library.enabled = True
    brain.visual_match_cache.enabled = True
    batches = []

    def observe():
        current = wire()  # Every call deliberately has a new observation/capture ID.
        shot = current.screenshot.model_copy(
            update={
                "width": image_size[0],
                "height": image_size[1],
                "geometry": current.screenshot.geometry.model_copy(
                    update={"physical_size": image_size}
                ),
            }
        )
        relay.current = current.model_copy(update={"screenshot": shot})
        return relay.current

    def screenshot(observation, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", image_size, "white").save(path)
        return path

    def act_batch(batch):
        batches.append(batch)
        post_id = observe().obs_id
        return ActionBatchResult(
            batch_id=batch.batch_id,
            ok=True,
            results=[
                ActionResult(
                    action_id=action.root.action_id, ok=True, post_obs_id=post_id, duration_ms=1
                )
                for action in batch.actions
            ],
            post_obs_id=post_id,
            duration_ms=2,
        )

    relay.observe, relay.screenshot, relay.act_batch = observe, screenshot, act_batch
    brain._observe()
    proposal = brain.visual_proposal
    assert proposal.steps[1].target is None
    assert brain._select_node() == "review"
    assert brain.visual_match_candidate is None
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation", brain.result
    assert sdk.messages.create.call_count == 1
    return brain, batches, proposal


def test_synthetic_provider_fill_and_focused_enter_share_batch_and_delivery_receipts(tmp_path):
    # The live response used target=""/point=[] for Enter; no account or inbox data is copied.
    response = {
        "screen_kind": "query",
        "screen_path": ["Synthetic mail", "Inbox"],
        "fields": [
            {
                "name": "Search mail",
                "role": "edit",
                "bbox": [100, 20, 200, 30],
                "source": "query.email",
                "value": "",
            }
        ],
        "steps": [
            {
                "action": "fill",
                "target": "Search mail",
                "intent": "search",
                "point": [175, 30],
                "source": "query.email",
            },
            {"action": "key", "target": "", "intent": "search", "point": [], "keys": "enter"},
        ],
        "purpose": "Enter the supplied query and submit it with current focus.",
    }
    brain, batches, proposal = replay_focused_key_response(tmp_path, json.dumps(response))
    assert len(batches) == 1
    actions = [action.root for action in batches[0].actions]
    assert [action.type for action in actions] == ["set_text", "key"]
    assert actions[0].text == "synthetic order"
    assert actions[1].keys == "enter" and actions[1].target is None
    assert actions[0].obs_id == actions[1].obs_id
    receipts = [
        event.payload
        for event in brain.log.read_all()
        if event.kind == "act" and event.payload.get("stage") == "returned"
    ]
    assert len(receipts) == 2 and all(receipt["ok"] for receipt in receipts)
    assert len({receipt["post_obs_id"] for receipt in receipts}) == 1
    assert not brain.memory_retrieval.state.staged
    assert (
        method_from_match(
            {"screen_phash": 0, "image_size": [400, 200]},
            proposal.mapping,
            method_id="synthetic-focus-key",
            purpose="Cannot invent geometry for current focus",
        )
        is None
    )


def test_focused_keys_do_not_remove_click_or_wheel_target_requirements():
    assert MatchAction(action="key", keys="enter", intent="search").target is None
    for action in (
        {"action": "click", "intent": "navigate", "point": [10, 10]},
        {"action": "key", "intent": "navigate", "keys": "pagedown"},
    ):
        with pytest.raises(ValidationError, match="screen_action_requires_target"):
            MatchAction(**action)
    for step in (
        {"action": "click", "target": "Button", "intent": "navigate", "point": []},
        {"action": "click", "intent": "navigate", "point": [10, 10]},
    ):
        with pytest.raises(ValidationError):
            ScreenMapping(fields={}, steps=[step])
