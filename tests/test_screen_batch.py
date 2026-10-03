"""Screen plans fill several controls before one joint reread."""

import hashlib
import json

import pytest
from test_brain import ScriptedModel, success_steps, values_for
from test_brain import record as record
from test_visual_workflow import SyntheticPerception, VisualFixture

from brain.graph.engine import Brain
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings


class ChangingPixelsRelay(VisualFixture):
    def observe(self):
        wire = super().observe()
        digest = hashlib.sha1(json.dumps([self.mode, self.query, self.inputs, self.saved]).encode())
        self.current = wire.model_copy(update={"screen_hash": digest.hexdigest()})
        return self.current


class CountPerception(SyntheticPerception):
    def __init__(self, relay):
        super().__init__(relay)
        self.action_counts = []

    def observe(self, wire):
        self.action_counts.append(len(self.relay.actions))
        scene = super().observe(wire)
        return scene.model_copy(update={"screen_kind": self.relay.mode})


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
    relay = ChangingPixelsRelay(record)
    brain, perception, model = make_brain(tmp_path, record, relay, responses)
    result = brain.run()
    assert result.status == "success", result
    assert relay.saved and set(brain.verified) == set(brain.values)
    # Three pre-entry actions, then eleven fields and one final focus move.
    assert not any(4 <= count <= 14 for count in perception.action_counts)
    assert perception.action_counts.count(15) == 1
    assert len(model.prompts) == 10  # Previously 20 including the initial plan.
    events = brain.log.read_all()
    checks = [e for e in events if e.payload.get("check") == "screen_fill_values"]
    assert len(checks) == 1 and len(checks[0].payload["matched"]) == 11
    assert not checks[0].payload["mismatched"]
    renewals = [e for e in events if e.payload.get("check") == "visual_planned_capture_renewal"]
    assert len(renewals) == 11  # Remaining ten inputs and the final focus move.
    assert all(e.payload["screen_batch"] and not e.payload["same_content"] for e in renewals)
    transitions = [
        e.payload["transition"] for e in events if e.payload.get("check") == "action_transition"
    ]
    field_proofs = [t for t in transitions if t["intent"] == "fill"]
    assert len(field_proofs) == 11
    assert all(t["observed_effect"] == "input_value_matches" for t in field_proofs)
    assert {f for t in field_proofs for f in t["new_facts"]} == set(brain.values)


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
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert brain._act_node({}) == {"next": "verify"}
    assert brain._verify_node({}) == {"next": "review"}
    assert set(brain.verified) == set(brain.values) - {"mbl_no"}
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert brain._act_node({}) == {"next": "verify"}
    assert brain._verify_node({}) == {"next": "review"}
    assert set(brain.verified) == set(brain.values)
    assert perception.action_counts == [0, 12, 14]


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
        assert brain._select_node({}) == {"next": "review"}
        assert brain._review_node({}) == {"next": "act"}
        assert brain._act_node({}) == {"next": "verify"}
        if wrong_ref:
            relay.inputs["hbl_no" if wrong_ref == "e20" else "mbl_no"] = "WRONG"
        assert brain._verify_node({}) == {"next": "review"}
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
        ] + ["unchecked"]


@pytest.mark.parametrize("failure", ["button", "duplicate_ref", "window_change", "move"])
def test_invalid_plan_and_mid_batch_geometry_change_stop_remaining_inputs(
    tmp_path, record, failure
):
    class DriftingRelay(ChangingPixelsRelay):
        def observe(self):
            wire = super().observe()
            if self.actions and failure == "window_change":
                wire = wire.model_copy(
                    update={
                        "active_window": wire.active_window.model_copy(
                            update={"title": "Different dialog"}
                        )
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
    selected = brain._select_node({})
    if selected["next"] == "review":
        selected = brain._review_node({})
    if selected["next"] == "act":
        brain._act_node({})
    assert len(relay.actions) == (1 if failure in {"window_change", "move"} else 0)
    assert not brain.verified and not relay.saved
    transitions = [
        e.payload["transition"]
        for e in brain.log.read_all()
        if e.payload.get("check") == "action_transition"
    ]
    assert len(transitions) == len(relay.actions)
    assert all(t["observed_effect"] == "unchecked" and not t["new_facts"] for t in transitions)
    assert brain.pending is None


@pytest.mark.parametrize("max_steps,delivered", [(11, 0), (12, 12)])
def test_shared_executor_checks_whole_plan_step_budget_before_delivery(
    tmp_path, record, max_steps, delivered
):
    relay = ChangingPixelsRelay(record)
    relay.mode = "editor"
    brain, perception, _ = make_brain(tmp_path, record, relay, [screen_plan(record)])
    brain.settings = brain.settings.model_copy(update={"max_steps": max_steps})
    brain.phase, brain.duplicate_clear = "enter", True
    brain._observe()
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert not relay.actions and perception.action_counts == [0]
    outcome = brain._act_node({})
    assert len(relay.actions) == delivered
    if delivered:
        assert outcome == {"next": "verify"}
        assert not brain.verified
        assert brain._verify_node({}) == {"next": "review"}
        assert perception.action_counts == [0, 12]
    else:
        assert outcome == {"next": "finish"}
        assert brain.result.reason == "max_steps"


@pytest.mark.parametrize("role", ["Cell", "Edit"])
def test_detail_row_cannot_substitute_for_flat_record_field(tmp_path, record, role):
    from test_brain import element

    from brain.cache.semantic import describe_target

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
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert brain._act_node({}) == {"next": "observe"}
    assert not relay.actions
    assert not brain._record_verified_field("description", cell)
    assert describe_target(cell, brain.observation) is None
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
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert not relay.actions
    assert brain._act_node({}) == {"next": "verify"}
    assert perception.action_counts == [0]
    assert [a.root.type for a in relay.actions] == ["set_text", "set_text", "click"]
    assert len(model.prompts) == 1
    outcome = brain._verify_node({})
    assert perception.action_counts == [0, 3]
    assert outcome == {"next": "review" if accept else "observe"}
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
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert brain._act_node({}) == {"next": "verify"}
    if wrong_value:
        relay.inputs["mbl_no"] = "WRONG"
    assert brain._verify_node({}) == {"next": "review"}
    assert set(brain.verified) == set(brain.values) - ({"mbl_no"} if wrong_value else set())


def test_model_only_screen_review_delegates_without_value_or_geometry_recheck(tmp_path, record):
    from brain.graph.execution import ActionPlan, PlanExecution
    from brain.graph.matching import ScreenMatch
    from brain.graph.schemas import Choice, Decision
    from brain.graph.screen_batch import verify_screen_fields

    relay = ChangingPixelsRelay(record)
    relay.mode = "editor"
    brain, _, _ = make_brain(tmp_path, record, relay, [])
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
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
    brain.visual_proposal = ScreenMatch(
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

    assert verify_screen_fields(brain, execution, current) == {"next": "review"}
    assert source not in brain.verified
    event = next(
        event
        for event in reversed(brain.log.read_all())
        if event.payload.get("check") == "screen_fill_values"
    )
    assert event.payload["reviewer"] == "screen_model"
    assert event.payload["delegated"] == [source]
    assert event.payload["ok"] is True


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
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert set(brain.verified) == set(brain.values)
