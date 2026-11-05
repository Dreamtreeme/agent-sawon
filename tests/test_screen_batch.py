"""Screen plans fill several controls before one joint reread."""

import hashlib
import json
from uuid import uuid4

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
        results = []
        for action in batch.actions:
            result = self.act(action)
            results.append(result)
            if not result.ok:
                break
        post_obs_id = next((result.post_obs_id for result in reversed(results) if result.ok), None)
        results = [
            result.model_copy(update={"post_obs_id": post_obs_id}) if result.ok else result
            for result in results
        ]
        ok = len(results) == len(batch.actions) and all(result.ok for result in results)
        return ActionBatchResult(
            batch_id=batch.batch_id,
            ok=ok,
            error=None if ok else results[-1].error,
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


class SyntheticLoginRelay(BatchedChangingPixelsRelay):
    """Current synthetic controls, with no desktop or provider access."""

    def __init__(self, record, *, accept=True, reject_password=False):
        super().__init__(record)
        self.mode = "login"
        self.user = self.password = ""
        self.accept = accept
        self.reject_password = reject_password

    def observe(self):
        from test_brain import element

        wire = super().observe()
        if self.mode == "login":
            self.items.extend(
                [
                    element("e14", "Edit", name="Account", value=self.user, parent="e0").model_copy(
                        update={"bbox": (140, 10, 8, 20)}
                    ),
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
        item = action.root
        x, _ = item.target.image_point
        if item.type == "set_text":
            if x == 144:
                self.user = item.text
            elif x == 84:
                if self.reject_password:
                    return ActionResult(
                        action_id=item.action_id,
                        ok=False,
                        error="focus_not_confirmed",
                        duration_ms=1,
                    )
                self.password = item.text
        submit = (item.type == "click" and x == 154) or (
            item.type == "key" and item.keys == "enter"
        )
        if submit and self.accept:
            assert self.user == "fixture-user" and self.password == "fixture-secret-901"
            self.mode = "query"
        return super().act(action)


def synthetic_login_plan(shape):
    """Reproduce the observed 3/5-action and keyboard login shapes using existing fakes."""
    user = {"source": "credential.username", "target": 14, "action": "fill", "intent": "credential"}
    password = {
        "source": "credential.password",
        "target": 8,
        "action": "fill",
        "intent": "credential",
    }
    submit = {"target": 15, "action": "click", "intent": "login", "point": [154, 20]}
    if shape == "focus_five":
        # The real five-action reply contains two focus clicks, normalized to navigate.
        steps = [
            {"target": 14, "action": "click", "intent": "navigate", "point": [144, 20]},
            user,
            {"target": 8, "action": "click", "intent": "navigate", "point": [84, 20]},
            password,
            submit,
        ]
    elif shape == "keyboard":
        steps = [
            user,
            {"target": 14, "action": "key", "keys": "tab", "intent": "navigate"},
            password,
            {"target": 8, "action": "key", "keys": "enter", "intent": "login"},
        ]
    else:
        steps = [user, password, submit]
    return {"screen_kind": "login", "procedure_step": "task-action", "controls": [], "steps": steps}


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


def make_brain(tmp_path, record, relay, responses, *, screen_matching=True):
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="screen-test")
    model = ScriptedModel(log, responses)
    brain = Brain(
        record=record,
        goal="Save for approval",
        relay=relay,
        model=model,
        settings=current_settings(tmp_path, screen_matching=screen_matching),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    perception = CountingScanner(relay, brain, len(responses) if screen_matching else 0)
    brain.screen_scanner = perception
    return brain, perception, model


def test_synthetic_three_inputs_then_scroll_observes_hidden_fourth_only_after_batch(
    tmp_path, record
):
    """Use synthetic screens with the actual compiler, executor and graph node methods."""
    from brain.task_instruction import TaskInstruction

    sources = list(values_for(record))[:4]

    class ScrollRelay(BatchedChangingPixelsRelay):
        lower = False

        def observe(self):
            wire = super().observe()
            visible = {"e0", *(self.field_refs[key] for key in sources[3:] if self.lower)}
            if not self.lower:
                visible.update(self.field_refs[key] for key in sources[:3])
            self.items = [item for item in self.items if item.ref in visible]
            return wire

        def act(self, action):
            if action.root.type != "scroll":
                return super().act(action)
            assert action.root.obs_id == self.current.obs_id
            assert action.root.target.capture_id == self.current.screenshot.geometry.capture_id
            assert action.root.wheel_delta == -600
            assert all(self.inputs[key] == self.values[key] for key in sources[:3])
            self.actions.append(action)
            self.lower = True
            return ActionResult(
                action_id=action.root.action_id, ok=True, post_obs_id=uuid4(), duration_ms=1
            )

    relay = ScrollRelay(record)
    relay.mode = "editor"
    first = {
        "screen_kind": "editor",
        "procedure_step": "task-action",
        "controls": [],
        "steps": [
            {"source": key, "target": index + 20, "action": "fill", "intent": "fill"}
            for index, key in enumerate(sources[:3])
        ]
        + [
            {
                "target": 22,
                "action": "key",
                "keys": "pagedown",
                "intent": "navigate",
                "point": [500, 50],
            }
        ],
    }
    second = {
        "screen_kind": "editor",
        "procedure_step": "task-action",
        "controls": [],
        "steps": [{"source": sources[3], "target": 23, "action": "fill", "intent": "fill"}],
    }
    brain, perception, model = make_brain(tmp_path, record, relay, [first, second])
    brain.task_instruction = TaskInstruction(
        name="Synthetic scroll input",
        objective="Enter four supplied values.",
        completion_conditions=["The four input fields show their supplied values."],
    )
    brain.phase = "task"
    brain._observe()
    assert not any(item.name == "Input 4" for item in brain.observation.elements)
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    before = relay.calls

    assert brain._act_node() == "observation"

    assert len(relay.batches) == 1
    assert relay.calls == before + 1, "renew once before the batch, never between its actions"
    assert [action.root.type for action in relay.batches[0].actions] == [
        "set_text",
        "set_text",
        "set_text",
        "scroll",
    ]
    assert relay.batches[0].actions[-1].root.target.image_point == (500, 50)
    capture = relay.batches[0].actions[0].root.target.capture_id
    assert {action.root.target.capture_id for action in relay.batches[0].actions} == {capture}
    assert relay.inputs[sources[3]] == ""
    assert len(model.prompts) == 1 and perception.action_counts == [0]

    assert brain._verify_node() == "review"
    assert perception.action_counts == [0, 4]
    assert any(item.name == "Input 4" for item in brain.observation.elements)
    assert len(model.prompts) == 2
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert relay.actions[-1].root.target.capture_id != capture
    assert all(relay.inputs[key] == relay.values[key] for key in sources)


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


@pytest.mark.parametrize("shape", ["three", "focus_five", "keyboard"])
@pytest.mark.parametrize("accept", [True, False])
def test_synthetic_login_uses_ordered_common_plan_before_one_reread(
    tmp_path, record, shape, accept
):
    relay = SyntheticLoginRelay(record, accept=accept)
    plan = synthetic_login_plan(shape)
    brain, perception, model = make_brain(tmp_path, record, relay, [plan])
    brain.settings = brain.settings.model_copy(update={"guard_mode": "enforce"})
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
    expected_types = {
        "three": ["set_text", "set_text", "click"],
        "focus_five": ["click", "set_text", "click", "set_text", "click"],
        "keyboard": ["set_text", "key", "set_text", "key"],
    }[shape]
    assert [a.root.type for a in relay.actions] == expected_types
    assert len(relay.batches) == 1
    assert len({a.root.target.capture_id for a in relay.batches[0].actions}) == 1
    assert set(brain.credential_submissions) == set(brain.credentials)
    assert len(brain.pending["login_plan_descriptors"]) == (2 if shape == "keyboard" else 3)
    assert len(model.prompts) == 1
    outcome = brain._verify_node()
    assert perception.action_counts == [0, len(expected_types)]
    assert outcome == ("review" if accept else "observation")
    assert not brain.save_issued and not brain._observed_inputs()
    assert "fixture-secret-901" not in (tmp_path / "run_log.jsonl").read_text()
    assert all("fixture-secret-901" not in prompt for prompt in model.prompts)


def test_synthetic_login_failed_password_keeps_only_acknowledged_credential(tmp_path, record):
    relay = SyntheticLoginRelay(record, reject_password=True)
    brain, perception, _ = make_brain(tmp_path, record, relay, [synthetic_login_plan("three")])
    brain.settings = brain.settings.model_copy(update={"guard_mode": "enforce"})
    brain.credentials = {
        "credential.username": "fixture-user",
        "credential.password": "fixture-secret-901",
    }
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert not brain.credential_submissions

    assert brain._act_node() == "observation"

    assert [action.root.type for action in relay.actions] == ["set_text"]
    assert set(brain.credential_submissions) == {"credential.username"}
    assert relay.mode == "login" and relay.password == ""
    assert brain.pending is None and perception.action_counts == [0]
    brain._observe()
    assert brain._decision_policy().facts.remaining_credentials == ("credential.password",)


def test_synthetic_typed_provider_login_uses_the_same_common_executor(tmp_path, record):
    """Exercise the actual nonmatching selection route, not a ScreenMatch adapter."""
    from brain.graph.screen_batch import ScreenFillPlan
    from brain.graph.screen_decision import compile_screen_action_plan

    relay = SyntheticLoginRelay(record)
    brain, perception, model = make_brain(tmp_path, record, relay, [], screen_matching=False)
    brain.settings = brain.settings.model_copy(update={"guard_mode": "enforce"})
    brain.credentials = {
        "credential.username": "fixture-user",
        "credential.password": "fixture-secret-901",
    }
    brain._observe()
    prefix = "e" + str(brain.observation.obs_id.int) + "0"
    typed = {
        "fields": [
            {"source": "credential.username", "ref": prefix + "14"},
            {"source": "credential.password", "ref": prefix + "8"},
        ],
        "next_decision": {
            "decision": {
                "screen_kind": "login",
                "operation": "act",
                "intent": "login",
                "subgoal": "Submit current login",
                "reason": "The visible button submits the form",
                "action": {"type": "click", "ref": prefix + "15"},
            }
        },
    }
    ScreenFillPlan.model_validate(typed)
    model.decisions = iter([typed])
    expected = compile_screen_action_plan(
        ScreenMatch.model_validate(synthetic_login_plan("three")), brain.observation
    )
    assert brain._select_node() == "review"
    actual = brain.action_plan
    assert [item.decision.action for item in actual.actions] == [
        item.decision.action for item in expected.actions
    ]
    assert [item.decision.intent for item in actual.actions] == [
        item.decision.intent for item in expected.actions
    ]
    assert actual.verification == expected.verification == "single"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert [action.root.type for action in relay.actions] == ["set_text", "set_text", "click"]
    assert set(brain.credential_submissions) == set(brain.credentials)
    assert len(model.prompts) == 1 and perception.action_counts == [0]
    assert brain._verify_node() == "review"
    assert perception.action_counts == [0, 3]


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
