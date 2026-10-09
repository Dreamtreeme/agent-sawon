"""Screen plans fill several controls before one joint reread."""

import hashlib
import json

import pytest
from sawon_interfaces import ActionBatchResult, ActionResult
from test_brain import ScriptedModel, success_steps, values_for
from test_brain import record as record
from test_visual_workflow import SyntheticPerception, VisualFixture

from brain.graph.engine import Brain
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings
from brain.visual.action_rebind import ActionTargetBinding


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


class CountPerception(SyntheticPerception):
    def __init__(self, relay):
        super().__init__(relay)
        self.action_counts = []

    def observe(self, wire):
        self.action_counts.append(len(self.relay.actions))
        scene = super().observe(wire)
        return scene.model_copy(update={"screen_kind": self.relay.mode})

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
        "fields": [{"source": f, "ref": f"e{i + 20}"} for i, f in enumerate(values_for(record))],
        "next_decision": None,
    }


def make_brain(tmp_path, record, relay, responses):
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="screen-test")
    model = ScriptedModel(log, responses)
    brain = Brain(
        record=record,
        goal="Save for approval",
        relay=relay,
        model=model,
        settings=BrainSettings(perception_mode="visual_only", visual_screen_fill=True),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    perception = CountPerception(relay)
    brain.visual_perception = perception
    return brain, perception, model


def test_complete_workflow_fills_all_fields_before_one_reread(tmp_path, record):
    ordinary = success_steps(record)
    batch = screen_plan(record)
    responses = ordinary[:4] + [batch] + ordinary[4 + len(batch["fields"]) :]
    relay = BatchedChangingPixelsRelay(record)
    brain, perception, model = make_brain(tmp_path, record, relay, responses)
    result = brain.run()
    assert result.status == "success", result
    assert relay.saved and set(brain.verified) == set(brain.values)
    # Three pre-entry actions, then eleven fields and one joint reread.
    assert not any(4 <= count <= 13 for count in perception.action_counts)
    assert perception.action_counts.count(14) == 1
    assert len(model.prompts) == 10  # Previously 20 including the initial plan.
    events = brain.log.read_all()
    checks = [e for e in events if e.payload.get("check") == "screen_fill_values"]
    assert len(checks) == 1 and len(checks[0].payload["matched"]) == 11
    assert not checks[0].payload["mismatched"]
    assert len(relay.batches) == 1 and len(relay.batches[0].actions) == 11
    fill_requests = [
        event
        for event in events
        if event.payload.get("stage") == "requested" and event.payload["action"]["reason"] == "fill"
    ]
    assert len(fill_requests) == 11
    assert not [
        event
        for event in events
        if fill_requests[0].seq < event.seq < fill_requests[-1].seq
        and event.payload.get("check") == "visual_identical_capture_renewal"
    ]
    batch_results = [e for e in events if e.payload.get("stage") == "batch_returned"]
    assert len(batch_results) == 1 and batch_results[0].payload["returned"] == 11
    transitions = [
        e.payload["transition"] for e in events if e.payload.get("check") == "action_transition"
    ]
    field_proofs = [t for t in transitions if t["intent"] == "fill"]
    assert len(field_proofs) == 11
    assert all(t["observed_effect"] == "input_value_matches" for t in field_proofs)
    assert {f for t in field_proofs for f in t["new_facts"]} == set(brain.values)


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
    brain.phase, brain.duplicate_clear = "enter", True
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
    brain.phase, brain.duplicate_clear = "enter", True
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    brain.phase_failures["enter"] = 1

    assert brain._act_node() == "observation"
    assert brain.pending is not None

    assert brain.phase_failures["enter"] == 0
    assert not brain.verified  # delivery is not semantic/business verification


def test_wrong_field_is_not_verified_and_only_that_field_is_replanned(tmp_path, record):
    class CorruptOnce(ChangingPixelsRelay):
        corrupt_once = True

        def act(self, action):
            result = super().act(action)
            if self.corrupt_once and self.inputs["mbl_no"]:
                self.inputs["mbl_no"] = "WRONG"
                self.corrupt_once = False
            return result

    relay = CorruptOnce(record)
    relay.mode = "editor"
    repair = {"fields": [{"source": "mbl_no", "ref": "e21"}], "next_decision": None}
    brain, perception, _ = make_brain(tmp_path, record, relay, [screen_plan(record), repair])
    brain.phase, brain.duplicate_clear = "enter", True
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert brain.pending is not None
    assert brain._verify_node() == "review"
    assert set(brain.verified) == set(brain.values) - {"mbl_no"}
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert brain.pending is not None
    assert brain._verify_node() == "review"
    assert set(brain.verified) == set(brain.values)
    assert perception.action_counts == [0, 11, 12]


def test_shared_source_verifies_and_repairs_each_delivered_target(tmp_path, record):
    from brain.graph.matching import ScreenMatch

    for wrong_ref in ("e20", "e21", None):
        relay = ChangingPixelsRelay(record)
        relay.mode = "editor"
        plan = {
            "fields": [{"source": "hbl_no", "ref": ref} for ref in ("e20", "e21")],
            "next_decision": None,
        }
        brain, perception, _ = make_brain(tmp_path / str(wrong_ref), record, relay, [plan])
        original = perception.observe
        staged = []
        brain._stage_visual_match_candidate = lambda pending, staged=staged: staged.append(pending)

        def observe(wire, original=original, relay=relay, wrong_ref=wrong_ref, brain=brain):
            scene = original(wire)
            if relay.actions:
                # One source-level evidence item must not stand for both distinct targets.
                good_ref = "e21" if wrong_ref == "e20" else "e20"
                target = int(good_ref[1:])
                prefix = "e" + str(scene.obs_id.int) + "0"
                scene = scene.model_copy(
                    update={
                        "elements": [
                            item.model_copy(update={"ref": prefix + str(target)})
                            if item.ref == good_ref
                            else item
                            for item in scene.elements
                        ]
                    }
                )
                brain.visual_proposal = ScreenMatch(
                    screen_kind="editor",
                    procedure_step="task-action",
                    controls=[],
                    operation="act",
                    evidence=[{"field": "hbl_no", "target": target}],
                )
            return scene

        perception.observe = observe
        brain.phase, brain.duplicate_clear = "enter", True
        brain._observe()
        assert brain._select_node() == "review"
        assert brain._review_node() == "execution"
        assert brain._act_node() == "observation"
        assert brain.pending is not None
        if wrong_ref:
            relay.inputs["hbl_no" if wrong_ref == "e20" else "mbl_no"] = "WRONG"
        assert brain._verify_node() == "review"
        assert ("hbl_no" in brain.verified) is (wrong_ref is None)
        assert len(staged) == (1 if wrong_ref is None else 0)
        if wrong_ref:
            assert brain.decision.action.ref == wrong_ref
        transitions = [
            event.payload["transition"]
            for event in brain.log.read_all()
            if event.payload.get("check") == "action_transition"
        ]
        assert [item["observed_effect"] for item in transitions] == [
            "unchecked" if ref == wrong_ref else "input_value_matches" for ref in ("e20", "e21")
        ]


@pytest.mark.parametrize("failure", ["button", "duplicate_ref", "process_change", "move"])
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
    if failure == "button":
        plan["fields"][-1]["ref"] = "e7"
    if failure == "duplicate_ref":
        plan["fields"][-1]["ref"] = "e20"
    brain, _, _ = make_brain(tmp_path, record, relay, [plan])
    brain.phase, brain.duplicate_clear = "enter", True
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
    assert not brain.verified and not relay.saved
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
    brain, perception, _ = make_brain(tmp_path, record, relay, [screen_plan(record)])
    brain.settings = brain.settings.model_copy(update={"max_steps": max_steps})
    brain.phase, brain.duplicate_clear = "enter", True
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert not relay.actions and perception.action_counts == [0]
    outcome = brain._act_node()
    assert len(relay.actions) == delivered
    if delivered:
        assert outcome == "observation"
        assert brain.pending is not None
        assert not brain.verified
        assert brain._verify_node() == "review"
        assert perception.action_counts == [0, 11]
    else:
        assert outcome == "finish"
        assert brain.result.reason == "max_steps"


@pytest.mark.parametrize("role", ["Cell", "Edit"])
def test_detail_row_cannot_substitute_for_flat_record_field(tmp_path, record, role):
    from test_brain import element

    relay = ChangingPixelsRelay(record)
    relay.mode = "editor"
    brain, _, _ = make_brain(
        tmp_path,
        record,
        relay,
        [
            {
                "fields": [{"source": "description", "ref": "e91"}],
                "next_decision": None,
            }
        ],
    )
    brain.phase, brain.duplicate_clear = "enter", True
    scene = brain._observe()
    table = element("e90", "Table", name="Optional detail lines")
    cell = element("e91", role, name="Description", value=brain.values["description"], parent="e90")
    brain.observation = scene.model_copy(update={"elements": [*scene.elements, table, cell]})
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert not relay.actions
    assert not brain._record_verified_field("description", cell)
    assert any(
        e.payload.get("reason") == "record_input_scope_mismatch" for e in brain.log.read_all()
    )


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
        "fields": [
            {"source": "credential.username", "ref": "e14"},
            {"source": "credential.password", "ref": "e8"},
        ],
        "login_ref": "e15",
        "next_decision": None,
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
    assert not brain.save_issued and not brain.verified
    assert "fixture-secret-901" not in (tmp_path / "run_log.jsonl").read_text()
    assert all("fixture-secret-901" not in prompt for prompt in model.prompts)


@pytest.mark.parametrize("wrong_value", [False, True])
def test_joint_verification_survives_corrected_visual_label(tmp_path, record, wrong_value):
    relay = ChangingPixelsRelay(record)
    relay.mode = "editor"
    brain, perception, _ = make_brain(tmp_path, record, relay, [screen_plan(record)])
    original = perception.observe

    def corrected_labels(wire):
        scene = original(wire)
        if relay.actions:
            scene = scene.model_copy(
                update={
                    "elements": [
                        e.model_copy(
                            update={"name": "Corrected caption", "label_hint": "Corrected"}
                        )
                        if e.role == "edit"
                        else e
                        for e in scene.elements
                    ]
                }
            )
        return scene

    perception.observe = corrected_labels
    brain.phase, brain.duplicate_clear = "enter", True
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert brain.pending is not None
    if wrong_value:
        relay.inputs["mbl_no"] = "WRONG"
    assert brain._verify_node() == "review"
    assert set(brain.verified) == set(brain.values) - ({"mbl_no"} if wrong_value else set())


def test_model_only_batch_consumes_observation_evidence_once(tmp_path, record):
    from brain.graph.execution import ActionPlan, PlanExecution
    from brain.graph.matching import ScreenMatch
    from brain.graph.schemas import Choice, Decision
    from brain.task_instruction import TaskInstruction

    relay = ChangingPixelsRelay(record)
    relay.mode = "editor"
    brain, _, _ = make_brain(tmp_path, record, relay, [])
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    brain.task_instruction = TaskInstruction(
        name="Enter shipment",
        objective="Enter the supplied shipment.",
        completion_conditions=["The shipment is saved."],
    )
    brain.phase = "task"
    scene = brain._observe()
    source = "hbl_no"
    target = 19
    prefix = "e" + str(scene.obs_id.int) + "0"
    original = next(element for element in scene.elements if element.role.casefold() == "edit")
    current = scene.model_copy(
        update={
            "elements": [
                original.model_copy(update={"ref": prefix + str(target), "value": "WRONG"})
                if element is original
                else element
                for element in scene.elements
            ]
        }
    )
    proposal = ScreenMatch(
        screen_kind="editor",
        procedure_step="task-action",
        controls=[],
        operation="act",
        evidence=[{"field": source, "target": target}],
    )
    fill = Decision(
        screen_kind="editor",
        operation="act",
        intent="fill",
        subgoal="task-action",
        reason="fill",
        action=Choice(type="set_text", ref=original.ref, source=source),
    )
    boundary = fill.model_copy(
        update={"intent": "navigate", "action": Choice(type="key", ref=original.ref, keys="tab")}
    )
    plan = ActionPlan.single(fill)
    execution = PlanExecution(plan, scene)
    execution.delivered = [
        {"choice": fill.action, "descriptor": {}},
        {"choice": boundary.action, "descriptor": {}},
    ]

    def observe_with_proposal(wire):
        brain.visual_proposal = proposal
        return current

    brain.visual_perception.observe = observe_with_proposal
    assert brain._observe() is current
    claims = brain.evidence_ledger.history
    assert len(claims) == 1

    assert brain._verify_screen_batch(execution, current) == "review"
    assert brain.evidence_ledger.history == claims
    recorded = [
        event
        for event in brain.log.read_all()
        if event.payload.get("check") == "model_task_evidence_recorded"
    ]
    assert len(recorded) == 1
    assert source in brain.verified
    event = next(
        event
        for event in reversed(brain.log.read_all())
        if event.payload.get("check") == "screen_fill_values"
    )
    assert event.payload["reviewer"] == "screen_model"
    assert event.payload["delegated"] == [source]
    assert event.payload["matched"] == [source]
    assert event.payload["ok"] is True
    assert brain.visual_proposal is None


def test_save_policy_uses_current_verified_evidence(tmp_path, record):
    from test_brain import decision

    relay = ChangingPixelsRelay(record)
    relay.mode = "editor"
    relay.inputs.update(values_for(record))
    proposal = {
        "fields": [],
        "next_decision": {
            "decision": decision(
                intent="save",
                kind="editor",
                action={"type": "click", "ref": "e7"},
                evidence=[
                    {"field": f, "ref": f"e{i + 20}"} for i, f in enumerate(values_for(record))
                ],
            )
        },
    }
    brain, _, _ = make_brain(tmp_path, record, relay, [proposal])
    brain.phase, brain.duplicate_clear = "enter", True
    brain._observe()
    assert not brain.verified
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert set(brain.verified) == set(brain.values)
