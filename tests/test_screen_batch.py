"""Screen plans fill several controls before one joint reread."""

import hashlib
import json

import pytest
from runtime_profile import current_settings
from sawon_interfaces import ActionBatchResult, ActionResult
from test_brain import ScriptedModel, values_for
from test_brain import record as record
from test_visual_workflow import SyntheticScanner, VisualFixture

from brain.graph.engine import Brain
from brain.graph.screen_decision import ScreenMatch
from brain.visual.action_rebind import ActionTargetBinding
from sawon_runtime.storage import RunLog


class ChangingPixelsRelay(VisualFixture):
    def observe(self):
        wire = super().observe()
        digest = hashlib.sha1(json.dumps([self.mode, self.query, self.inputs, self.saved]).encode())
        self.current = wire.model_copy(update={"screen_hash": digest.hexdigest()})
        return self.current


class BatchedChangingPixelsRelay(ChangingPixelsRelay):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.batches = []

    def act_batch(self, batch):
        self.batches.append(batch)
        results = [self.act(action) for action in batch.actions]
        post_obs_id = results[-1].post_obs_id
        results = [result.model_copy(update={"post_obs_id": post_obs_id}) for result in results]
        return ActionBatchResult(
            batch_id=batch.batch_id,
            ok=True,
            results=results,
            post_obs_id=post_obs_id,
            duration_ms=sum(result.duration_ms for result in results),
        )


class CountingScanner(SyntheticScanner):
    def __init__(self, relay, brain, response_count):
        super().__init__(relay)
        self.brain = brain
        self.response_count = response_count
        self.action_counts = []

    def scan(self, wire):
        self.action_counts.append(len(self.relay.actions))
        scene = super().scan(wire)
        prefix = "e" + str(scene.obs_id.int) + "0"
        scene = scene.model_copy(
            update={
                "screen_kind": self.relay.mode,
                "elements": [
                    item.model_copy(
                        update={
                            "ref": prefix + item.ref[1:],
                            "parent_ref": prefix + item.parent_ref[1:] if item.parent_ref else None,
                        }
                    )
                    for item in scene.elements
                ],
            }
        )
        if self.response_count:
            self.response_count -= 1
            self.brain.visual_proposal = self.brain._call(
                "Current synthetic screen: observe values and plan the next action", ScreenMatch
            )
        return scene

    def rebind_action(self, scene, target, fresh, *, planned_point=None, max_phash_distance=16):
        """The synthetic relay exposes its current local detections directly."""
        matches = [
            item
            for item in self.relay.items
            if item.role.casefold() == target.role.casefold()
            and item.name == target.name
            and item.label_hint == target.label_hint
        ]
        if len(matches) != 1:
            raise ValueError("visual_target_anchor_ambiguous")
        current = matches[0]
        x, y, width, height = current.bbox
        return ActionTargetBinding(
            point=(x + width / 2, y + height / 2),
            bbox=tuple(map(int, current.bbox)),
            method="synthetic_local_rebind",
            phash_distance=0,
        )


def screen_plan(record):
    return {
        "screen_kind": "editor",
        "procedure_step": "task-action",
        "controls": [],
        "steps": [
            {"source": field, "target": i + 20, "action": "fill", "intent": "fill"}
            for i, field in enumerate(values_for(record))
        ],
    }


def make_brain(tmp_path, record, relay, responses):
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="screen-test")
    model = ScriptedModel(log, responses)
    brain = Brain(
        record=record,
        goal="Save for approval",
        relay=relay,
        model=model,
        settings=current_settings(tmp_path),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    perception = CountingScanner(relay, brain, len(responses))
    brain.screen_scanner = perception
    return brain, perception, model


def test_direct_fill_batch_records_delivered_prefix_on_relay_failure(tmp_path, record):
    class PartialBatch(BatchedChangingPixelsRelay):
        def act_batch(self, batch):
            self.batches.append(batch)
            results = [self.act(action) for action in batch.actions[:3]]
            rejected = batch.actions[3].root
            results.append(
                ActionResult(
                    action_id=rejected.action_id,
                    ok=False,
                    error="focus_not_confirmed",
                    duration_ms=1,
                )
            )
            return ActionBatchResult(
                batch_id=batch.batch_id,
                ok=False,
                error="focus_not_confirmed",
                results=results,
                duration_ms=2,
            )

    relay = PartialBatch(record)
    relay.mode = "editor"
    brain, _, _ = make_brain(tmp_path, record, relay, [screen_plan(record)])
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert len(relay.actions) == 3
    assert brain.pending is None
    transitions = [
        event.payload["transition"]
        for event in brain.log.read_all()
        if event.payload.get("check") == "action_transition"
    ]
    assert len(transitions) == 3
    assert all(item["observed_effect"] == "unchecked" for item in transitions)


def test_successful_input_delivery_separates_unrelated_phase_failures(tmp_path, record):
    relay = BatchedChangingPixelsRelay(record)
    relay.mode = "editor"
    brain, _, _ = make_brain(tmp_path, record, relay, [screen_plan(record)])
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    brain.phase_failures["task"] = 1

    assert brain._act_node() == "observation"
    assert brain.pending is not None

    assert brain.phase_failures["task"] == 0
    assert not brain._observed_inputs()  # delivery is not semantic/business verification


@pytest.mark.parametrize("failure", ["duplicate_ref", "process_change", "move"])
def test_invalid_plan_and_mid_batch_geometry_change_stop_remaining_inputs(
    tmp_path, record, failure
):
    class DriftingRelay(ChangingPixelsRelay):
        def observe(self):
            wire = super().observe()
            if self.actions and failure == "process_change":
                changed = wire.active_window.model_copy(update={"process": "other.exe"})
                wire = wire.model_copy(
                    update={
                        "active_window": changed,
                        "windows": [changed],
                    }
                )
            if self.actions and failure == "move":
                geometry = wire.screenshot.geometry.model_copy(update={"origin": (200, 200)})
                wire = wire.model_copy(
                    update={"screenshot": wire.screenshot.model_copy(update={"geometry": geometry})}
                )
            self.current = wire
            return wire

    relay = DriftingRelay(record)
    relay.mode = "editor"
    plan = screen_plan(record)
    if failure == "duplicate_ref":
        plan["steps"][-1]["target"] = 20
    brain, _, _ = make_brain(tmp_path, record, relay, [plan])
    brain._observe()
    selected = brain._select_node()
    if selected == "review":
        selected = brain._review_node()
    outcome = selected
    if selected == "execution":
        outcome = brain._act_node()
    assert len(relay.actions) == (
        1 if failure == "process_change" else 11 if failure == "move" else 0
    )
    if failure == "move":
        assert outcome == "observation"
        assert brain.pending is not None
        return
    assert not brain._observed_inputs() and not relay.saved
    transitions = [
        e.payload["transition"]
        for e in brain.log.read_all()
        if e.payload.get("check") == "action_transition"
    ]
    assert len(transitions) == len(relay.actions)
    assert all(t["observed_effect"] == "unchecked" and not t["new_facts"] for t in transitions)
    assert brain.pending is None


@pytest.mark.parametrize("max_steps,delivered", [(10, 0), (11, 11)])
def test_shared_executor_checks_whole_plan_step_budget_before_delivery(
    tmp_path, record, max_steps, delivered
):
    relay = ChangingPixelsRelay(record)
    relay.mode = "editor"
    observed_values = {
        "screen_kind": "editor",
        "procedure_step": "task-action",
        "controls": [],
        "mapping": {
            "fields": {
                source: {
                    "source": source,
                    "role": "edit",
                    "value": value,
                    "bbox": [(index + 20) * 10, 10, 8, 20],
                }
                for index, (source, value) in enumerate(values_for(record).items())
            }
        },
    }
    brain, perception, _ = make_brain(
        tmp_path, record, relay, [screen_plan(record), observed_values]
    )
    brain.settings = brain.settings.model_copy(update={"max_steps": max_steps})
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert not relay.actions and perception.action_counts == [0]
    outcome = brain._act_node()
    assert len(relay.actions) == delivered
    if delivered:
        assert outcome == "observation"
        assert brain.pending is not None
        assert not brain._observed_inputs()
        assert brain._verify_node() == "review"
        assert perception.action_counts == [0, 11]
    else:
        assert outcome == "finish"
        assert brain.result.reason == "max_steps"


@pytest.mark.parametrize("accept", [True, False])
def test_login_plan_sends_two_credentials_and_click_before_any_reread(tmp_path, record, accept):
    from test_brain import element

    class LoginVisual(ChangingPixelsRelay):
        user = ""
        password = ""

        def observe(self):
            wire = super().observe()
            if self.mode == "login":
                self.items.extend(
                    [
                        element(
                            "e14", "Edit", name="Account", value=self.user, parent="e0"
                        ).model_copy(update={"bbox": (140, 10, 8, 20)}),
                        element("e15", "Button", name="Sign in", parent="e0").model_copy(
                            update={"bbox": (150, 10, 8, 20)}
                        ),
                    ]
                )
            digest = hashlib.sha1(
                json.dumps([wire.screen_hash, self.user, bool(self.password)]).encode()
            )
            self.current = wire.model_copy(update={"screen_hash": digest.hexdigest()})
            return self.current

        def act(self, action):
            x, _ = action.root.target.image_point
            if action.root.type == "set_text":
                if x == 144:
                    self.user = action.root.text
                elif x == 84:
                    self.password = action.root.text
            if x == 154 and action.root.type == "click" and accept:
                assert self.user == "fixture-user" and self.password == "fixture-secret-901"
                self.mode = "query"
            return super().act(action)

    relay = LoginVisual(record)
    relay.mode = "login"
    plan = {
        "screen_kind": "login",
        "procedure_step": "task-action",
        "controls": [],
        "steps": [
            {
                "source": "credential.username",
                "target": 14,
                "action": "fill",
                "intent": "credential",
            },
            {
                "source": "credential.password",
                "target": 8,
                "action": "fill",
                "intent": "credential",
            },
            {"target": 15, "action": "click", "intent": "login", "point": [154, 20]},
        ],
    }
    brain, perception, model = make_brain(tmp_path, record, relay, [plan])
    brain.credentials = {
        "credential.username": "fixture-user",
        "credential.password": "fixture-secret-901",
    }
    brain.secrets = ("fixture-secret-901",)
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert not relay.actions
    assert brain._act_node() == "observation"
    assert brain.pending is not None
    assert perception.action_counts == [0]
    assert [a.root.type for a in relay.actions] == ["set_text", "set_text", "click"]
    assert len(model.prompts) == 1
    outcome = brain._verify_node()
    assert perception.action_counts == [0, 3]
    assert outcome == ("review" if accept else "observation")
    assert not brain.save_issued and not brain._observed_inputs()
    assert "fixture-secret-901" not in (tmp_path / "run_log.jsonl").read_text()
    assert all("fixture-secret-901" not in prompt for prompt in model.prompts)


def test_synthetic_model_observation_preserves_next_plan_without_batch_coverage_gate(
    tmp_path, record
):
    """Existing fake supplies omitted, conflicting, then matching readings after a real fill."""
    from brain.graph.screen_mapping import ScreenMapping
    from brain.task_instruction import TaskInstruction

    source = "hbl_no"
    target = 20
    expected = values_for(record)[source]
    initial = screen_plan(record)
    initial["steps"] = initial["steps"][:1]
    initial["mapping"] = ScreenMapping(
        screen_path=["Shipment", "Entry"],
        fields={
            "Reference": {"source": source, "role": "edit", "bbox": [200, 10, 8, 20], "value": ""}
        },
    ).model_dump(exclude_unset=True)
    responses = [initial]
    for reading in (None, "WRONG CURRENT VALUE", expected):
        field = {"source": source, "role": "edit", "bbox": [200, 10, 8, 20]}
        if reading is not None:
            field["value"] = reading
        responses.append(
            {
                "screen_kind": "editor",
                "procedure_step": "task-action",
                "controls": [],
                "mapping": {
                    "screen_path": ["Shipment", "Entry"],
                    "fields": {"Reference": field},
                },
                "steps": [{"target": target, "action": "key", "keys": "tab", "intent": "navigate"}],
            }
        )
    relay = ChangingPixelsRelay(record)
    relay.mode = "editor"
    brain, _, model = make_brain(tmp_path, record, relay, responses)
    brain.task_instruction = TaskInstruction(
        name="Enter shipment",
        objective="Enter the supplied shipment.",
        completion_conditions=["The shipment is saved."],
    )
    brain.phase = "task"
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    execution = brain.pending["execution"]
    field = brain.screen_nodes[json.dumps(["Shipment", "Entry"])]["fields"][source]
    delivery = dict(field["delivery"])
    assert delivery["value"] == expected
    assert field["last_observation"]["value"] == ""
    assert source not in brain._observed_inputs(), "delivery is not an observed value"

    for label, expected_reading in (
        ("omitted", ""),
        ("mismatch", "WRONG CURRENT VALUE"),
        ("matching", expected),
    ):
        current = brain._observe()
        proposal = brain.visual_proposal
        field = brain.screen_nodes[json.dumps(["Shipment", "Entry"])]["fields"][source]
        assert field["last_observation"]["value"] == expected_reading, label
        assert field["delivery"] == delivery, label
        assert (source in brain._observed_inputs()) is (label == "matching"), label
        assert brain._verify_screen_batch(execution, current) == ("review", None), label
        assert brain.visual_proposal is proposal
        assert brain.failures == {}, label
    assert len(model.prompts) == 4
    assert not any(
        event.payload.get("check") == "screen_fill_values"
        or event.payload.get("reason") == "screen_fill_evidence_missing"
        for event in brain.log.read_all()
    )
