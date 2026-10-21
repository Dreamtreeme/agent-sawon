"""Procedure-driven OCR matching shares one response with the existing executor."""

import json
from dataclasses import replace
from hashlib import sha256
from itertools import repeat
from pathlib import Path

import pytest
from PIL import Image
from test_brain import ScriptedModel
from test_brain import record as record
from test_visual_markers import fixture
from test_visual_scene import wire

import brain.graph.context_recall as matching
from brain.cache import ScreenPatternCache, screen_pattern_key
from brain.graph.context_recall import (
    CONTEXT_PLAN_RULES,
    OBSERVE_ONLY_TARGET_RULES,
    TARGET_GROUNDING_RULES,
    ActionCapability,
    ContextRecallServices,
    RecallSelection,
    ScreenMatch,
    ScreenPlan,
    bind_value_evidence_to_controls,
    build_context_prompt,
    compile_match,
    compile_screen_action_plan,
    enforce_taught_input_surfaces,
    omit_nonblocking_image_request,
    omit_nonblocking_refinement,
    recall_context,
    select_context_response,
    task_action_contract,
    validate_action_surfaces,
    validate_match_plan,
)
from brain.graph.engine import Brain
from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.graph.schemas import DecisionResponse
from brain.graph.screen_batch import LoginScreenPlan, ScreenFillPlan
from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES
from brain.settings import BrainSettings
from brain.task_instruction import TaskInstruction
from brain.visual.scene import SceneObservation, SemanticDetection
from sawon_runtime.storage import RunLog


@pytest.fixture
def task_instruction():
    return TaskInstruction.model_validate_json(
        (Path(__file__).resolve().parents[1] / "examples/shipment-task.json").read_bytes()
    )


def test_ocr_fill_target_is_rejected_as_non_action_surface(tmp_path):
    class BrainStub:
        log = RunLog(tmp_path, case_id="defer-ocr-fill", agent_id="test")

    response = ScreenMatch.model_validate(
        {
            "screen_kind": "editor",
            "procedure_step": "task-action",
            "controls": [
                {"id": 1, "role": "edit", "confidence": 1},
                {"id": 2, "role": "edit", "text_ids": [8], "confidence": 1},
            ],
            "steps": [
                {"target": 1, "action": "fill", "intent": "fill", "source": "safe"},
                {
                    "target": 2,
                    "action": "fill",
                    "intent": "fill",
                    "source": "missing",
                },
            ],
        }
    )
    screen = {
        "markers": [
            {"id": 1, "source": "omniparser"},
            {"id": 2, "source": "ocr"},
        ]
    }

    with pytest.raises(ValueError, match="screen_match_action_surface_mismatch"):
        validate_action_surfaces(BrainStub(), response, screen)
    event = BrainStub.log.read_all()[-1]
    assert event.payload == {
        "check": "action_surface_rejected",
        "targets": [
            {
                "target": 2,
                "action": "fill",
                "source": "ocr",
                "role": "edit",
                "reason": "action_not_allowed_for_geometry",
            }
        ],
    }


def test_ocr_text_click_proxy_is_actionable_without_claiming_a_control_role(tmp_path):
    class BrainStub:
        log = RunLog(tmp_path, case_id="ocr-text-click", agent_id="test")

    response = ScreenMatch.model_validate(
        {
            "screen_kind": "editor",
            "procedure_step": "task-action",
            "controls": [{"id": 2, "role": "text", "text_ids": [8], "confidence": 1}],
            "steps": [
                {
                    "target": 2,
                    "action": "click",
                    "point": [10, 10],
                    "intent": "select_tab",
                }
            ],
        }
    )
    screen = {"markers": [{"id": 2, "source": "ocr"}]}

    assert validate_action_surfaces(BrainStub(), response, screen) is response
    assert BrainStub.log.read_all() == []


def setup_brain(tmp_path, record, responses, *, task_instruction=None):
    """Initialize through the same task loader used by the screen worker."""
    blocks, boxes, _, meaning = fixture()

    class Relay:
        color = "white"

        def observe(self):
            self.current = wire()
            return self.current

        def screenshot(self, observed, path):
            path.parent.mkdir(parents=True)
            Image.new("RGB", (400, 200), self.color).save(path)
            return path

    class Ocr:
        calls = 0

        def read(self, page):
            self.calls += 1
            return blocks

    class Detector:
        calls = 0

        def read(self, page, *, ocr_boxes=()):
            self.calls += 1
            return boxes

    relay, ocr, detector = Relay(), Ocr(), Detector()
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="test")
    plans = iter(responses)

    def route(prompt):
        item = next(plans)
        if prompt.startswith((CONTEXT_PLAN_RULES, CONTEXT_PLAN_RULES)):
            return {key: value for key, value in item.items() if key in ScreenPlan.model_fields}
        if prompt.startswith(CONTEXT_MAPPING_RULES):
            assert "fields" in item, "Provider fixtures must use semantic names and coordinates"
        if prompt.startswith(TARGET_GROUNDING_RULES):
            return {
                key: value for key, value in item.items() if key in SemanticDetection.model_fields
            }
        return item

    model = ScriptedModel(log, repeat(route))
    if task_instruction is None:
        task_instruction = TaskInstruction.model_validate_json(
            (Path(__file__).resolve().parents[1] / "examples/shipment-task.json").read_bytes()
        )
    task_path = tmp_path / "user-task.json"
    task_path.write_text(task_instruction.model_dump_json(), encoding="utf-8")
    brain = Brain(
        record=record,
        goal="Register synthetic shipment",
        relay=relay,
        model=model,
        settings=BrainSettings(
            screen_matching=True,
            visual_screen_fill=True,
            task_path=task_path,
        ),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=ocr,
    )
    brain.screen_scanner.detector = detector
    return brain, model, relay, ocr, detector, meaning


def matching_contract(brain, screen=None):
    screen = screen or {"ocr": [], "markers": []}
    services = ContextRecallServices(
        settings=brain.settings,
        log=brain.log,
        call=brain._call,
        case_dir=brain.case_dir,
        cache=brain.visual_match_cache,
        memory_retrieval=brain.memory_retrieval,
    )
    return brain._context_recall_request(screen, None), services


def test_method_grounding_failure_retries_visual_cache_or_model_once(
    tmp_path, record, task_instruction, monkeypatch
):
    brain, _, _, _, _, _ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    request, services = matching_contract(brain)
    method_response = ScreenMatch(
        screen_kind="editor",
        procedure_step="task-action",
        purpose="reused method",
        controls=[],
        steps=[],
    )
    fallback_response = method_response.model_copy(update={"purpose": "fallback"})
    selections = []

    def select(_request, _services, _prepared, *, allow_method=True):
        selections.append(allow_method)
        if allow_method:
            return RecallSelection(method_response, None, method_response, False, _prepared)
        return RecallSelection(fallback_response, ("f" * 64, {}), None, False, _prepared)

    def ground(response, *_args, **_kwargs):
        if response is method_response:
            raise ValueError("input_label_not_near_control")
        return response

    rejected = []

    def reject_pending(*, reason):
        rejected.append(reason)
        return {"method_id": "method-1", "group_id": "group-1"}

    monkeypatch.setattr(matching, "select_context_response", select)
    monkeypatch.setattr(matching, "validate_or_replan", lambda response, *_: response)
    monkeypatch.setattr(matching, "ground_context_response", ground)
    monkeypatch.setattr(services.memory_retrieval, "reject_pending", reject_pending)

    result = recall_context(request, services)

    assert result.response.purpose == "fallback"
    assert selections == [True, False]
    assert rejected == ["input_label_not_near_control"]
    event = next(
        item for item in brain.log.read_all() if item.payload.get("check") == "method_fallback"
    )
    assert event.payload["next"] == "visual_cache_or_model"


def response(meaning, **kwargs):
    from brain.visual.marker_contract import model_controls

    return {
        **meaning.model_dump(),
        "controls": model_controls(
            meaning.controls, {"markers": [m.model_dump() for m in fixture()[2]]}
        ),
        "screen_kind": "query",
        "procedure_step": "enter-duplicate-key",
        "steps": [
            {
                "target": meaning.controls[0].id,
                "action": "fill",
                "source": "hbl_no",
                "intent": "query_input",
            }
        ],
        **kwargs,
    }


def named_response(**overrides):
    """Provider fixture: semantic names and chosen geometry, with no detector handles."""
    return {
        "screen_kind": "query",
        "fields": {"Reference": {"role": "edit", "bbox": [100, 15, 150, 30], "value": ""}},
        "assignments": {"Reference": "hbl_no"},
        "procedure_step": "enter-duplicate-key",
        "steps": [
            {
                "target": "Reference",
                "action": "fill",
                "source": "hbl_no",
                "intent": "query_input",
                "point": [175, 30],
            }
        ],
        **overrides,
    }


def test_task_is_loaded_and_new_screen_match_is_one_combined_call(tmp_path, record):
    brain, model, _, _, _, _ = setup_brain(
        tmp_path, record, [named_response()], task_instruction=None
    )
    brain.credentials = {"credential.username": "secret-user", "credential.password": "secret-pw"}
    assert brain._plan_node() == "observation"
    assert not model.prompts
    assert json.loads((tmp_path / "task-instruction.json").read_text(encoding="utf-8"))["objective"]
    brain._observe()
    assert brain._select_node() == "review"
    assert brain.review_execution
    assert not any(event.kind == "act" for event in brain.log.read_all())
    assert brain._review_node() == "execution"
    assert not brain.review_execution
    assert any(
        event.payload.get("check") == "proposed_execution_review"
        and event.payload.get("stage") == "before_action"
        for event in brain.log.read_all()
    )
    assert len(model.prompts) == 1
    assert model.prompts[0].startswith(CONTEXT_MAPPING_RULES)
    assert OBSERVE_ONLY_TARGET_RULES not in model.prompts[0]
    assert brain.action_plan.actions[0].decision.action.source == "hbl_no"
    assert brain.action_plan.actions[0].decision.action.ref.startswith(
        "e" + str(brain.observation.obs_id.int)
    )
    assert "secret-user" not in model.prompts[0] and "secret-pw" not in model.prompts[0]


def test_named_current_evidence_is_bound_without_a_second_grounding_call(
    tmp_path, record, task_instruction
):
    data = named_response()
    data["fields"].update(
        {
            "Master": {
                "role": "text",
                "bbox": [10, 20, 60, 15],
                "value": record.fields.mbl_no.value,
            },
            "Shipper": {
                "role": "text",
                "bbox": [10, 45, 60, 15],
                "value": record.fields.shipper.value,
            },
        }
    )
    data["evidence"] = [
        {"field": "mbl_no", "target": "Master"},
        {"field": "shipper", "target": "Shipper"},
    ]
    brain, model, _, ocr, detector, _ = setup_brain(
        tmp_path, record, [data], task_instruction=task_instruction
    )
    brain._observe()
    assert len(model.prompts) == 1
    assert [item.field for item in brain.visual_proposal.evidence] == ["mbl_no", "shipper"]
    assert ocr.calls == detector.calls == 1
    assert brain.saved_bindings[1]["mbl_no"] in {e.ref for e in brain.observation.elements}
    assert "mbl_no" not in brain.verified  # Binding alone does not claim value verification.


def test_task_instruction_exposes_goal_and_capabilities_without_fixed_ui_route(
    tmp_path, record, task_instruction
):
    fixture()[-1]
    task_response = named_response(
        procedure_step="로그인",
    )
    task_response["steps"][0]["source"] = record.fields.hbl_no.value
    brain, model, *_ = setup_brain(
        tmp_path, record, [task_response], task_instruction=task_instruction
    )
    assert brain.phase == "task"
    assert not (tmp_path / "procedure.json").exists()
    assert brain.task_instruction == task_instruction
    assert (
        brain.task_instruction_sha256 == sha256(brain.settings.task_path.read_bytes()).hexdigest()
    )
    assert (
        TaskInstruction.model_validate_json((tmp_path / "task-instruction.json").read_bytes())
        == task_instruction
    )
    education = next(
        event
        for event in brain.log.read_all()
        if event.payload.get("check") == "user_education_loaded"
    )
    assert education.payload["sha256"] == brain.task_instruction_sha256
    brain.verified["hbl_no"] = "prior-screen-ref"

    assert brain._plan_node() == "observation"
    brain._observe()

    assert len(model.prompts) == 1
    assert model.prompts[0].startswith(CONTEXT_MAPPING_RULES)
    state = json.loads(model.prompts[0][len(CONTEXT_MAPPING_RULES) :])
    assert state["task"]["objective"] == brain.task_instruction.objective
    assert state["task"]["procedure"] == []
    assert state["taught_procedure_progress"]["steps"] == []
    assert state["taught_procedure_progress"]["completed_step_ids"] == []
    assert state["notebook"]["inputs"] == brain.values
    assert state["notebook"]["verified_inputs"] == ["hbl_no"]
    assert state["notebook"]["available_inputs"] == sorted(brain.values)
    assert state["notebook"]["remaining_inputs"] == sorted(set(brain.values) - {"hbl_no"})
    assert "record" not in state["notebook"]
    assert "remaining_fields" not in state["notebook"]
    persisted = json.loads((tmp_path / "task-notebook.json").read_text(encoding="utf-8"))
    assert persisted["inputs_path"] == "record.json"
    assert persisted["available_inputs"] == sorted(brain.values)
    assert persisted["remaining_inputs"] == sorted(set(brain.values) - {"hbl_no"})
    assert "record_path" not in persisted
    assert "query_submitted" not in persisted
    assert state["capability"]["capability_id"] == "task-action"
    assert state["capability"]["input_scope"] == sorted(brain.values)
    assert "field_scope" not in state["capability"]
    assert "procedure" not in state
    assert brain.visual_proposal.procedure_step == "task-action"
    assert brain.visual_proposal.steps[0].intent == "fill"
    assert brain._select_node() == "review"
    assert brain.action_plan.actions[0].decision.action.source == "hbl_no"
    assert not {
        "open-shipper-lookup",
        "lookup-partner",
        "open-additional-entry",
        "fill-additional-entry",
    } & {state["capability"]["capability_id"]}


@pytest.mark.parametrize(
    ("source", "intent"), [("record.hbl_no", "fill"), ("inputs.hbl_no", "search")]
)
def test_generic_task_binds_prompt_container_path_to_input_key(
    tmp_path, record, source, intent, task_instruction
):
    task_response = named_response(procedure_step="Enter the visible task input")
    task_response["steps"][0]["source"] = source
    task_response["steps"][0]["intent"] = intent
    task_response["evidence"] = [{"field": source, "target": "Reference"}]
    task_response["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    brain, *_ = setup_brain(tmp_path, record, [task_response], task_instruction=task_instruction)

    brain._observe()

    assert brain.visual_proposal.procedure_step == "task-action"
    assert brain.visual_proposal.steps[0].intent == "fill"
    assert brain.visual_proposal.steps[0].source == "hbl_no"
    assert brain.visual_proposal.evidence[0].field == "hbl_no"


def test_model_plan_trusts_model_semantics_within_the_runtime_input_scope(tmp_path, record):
    task_instruction = TaskInstruction.model_validate(
        {
            "name": "Taught shipment entry",
            "objective": "Enter the visible shipment fields.",
            "completion_conditions": ["The fields match."],
            "procedure": [
                {
                    "id": "fill-basic",
                    "instruction": "Enter both reference numbers.",
                    "completion": "Both reference numbers match.",
                    "actions": [
                        {
                            "verb": "fill",
                            "target": "House B/L number field",
                            "input_key": "hbl_no",
                        },
                        {
                            "verb": "fill",
                            "target": "Master B/L field",
                            "input_key": "mbl_no",
                        },
                    ],
                }
            ],
        }
    )
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    screen = {"ocr": [{"id": 5, "text": "House B/L number"}], "markers": []}
    request, services = matching_contract(brain, screen)
    capability = ActionCapability(
        actions=["fill"],
        input_scope=["hbl_no", "mbl_no"],
        goal="Enter both references",
        completion="Both references match",
    )

    def planned(source):
        return ScreenMatch.model_validate(
            {
                "screen_kind": "editor",
                "procedure_step": "task-action",
                "taught_step_id": "fill-basic",
                "controls": [{"id": 1, "role": "edit", "label_ids": [5], "confidence": 1}],
                "steps": [{"target": 1, "action": "fill", "intent": "fill", "source": source}],
            }
        )

    accepted = validate_match_plan(
        planned("mbl_no"), capability, request, services, set(brain.values)
    )
    assert accepted.steps[0].source == "mbl_no"


def test_literal_value_evidence_is_bound_to_its_unique_input_control(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    screen = {
        "ocr": [{"id": 8, "text": "DEMO-HBL-001"}],
        "markers": [{"id": 18, "source": "ocr", "text_ids": [8]}],
    }
    _, services = matching_contract(brain, screen)
    response = ScreenMatch.model_validate(
        {
            "screen_kind": "editor",
            "procedure_step": "task-action",
            "operation": "stop",
            "controls": [{"id": 14, "role": "edit", "text_ids": [8], "confidence": 1}],
            "evidence": [{"field": "hbl_no", "target": 18}],
        }
    )

    rebound = bind_value_evidence_to_controls(services, response, screen)

    assert rebound.evidence[0].target == 14
    event = brain.log.read_all()[-1]
    assert event.payload == {
        "check": "value_evidence_bound_to_control",
        "bindings": [{"field": "hbl_no", "value_marker": 18, "control": 14}],
    }


def test_post_save_query_rejects_navigation_to_a_caption_instead_of_identity_row(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.saved_evidence.begin("saved")
    screen = {
        "ocr": [
            {"id": 3, "text": "House B/L"},
            {"id": 4, "text": record.fields.hbl_no.value},
        ],
        "markers": [
            {"id": 12, "source": "ocr", "text_ids": [3]},
            {"id": 30, "source": "ocr", "text_ids": [4]},
        ],
    }
    request, services = matching_contract(brain, screen)
    capability = ActionCapability(
        actions=["navigate"], goal="Open saved result", completion="Saved detail opens"
    )

    def planned(target, text_id):
        return ScreenMatch.model_validate(
            {
                "screen_kind": "query",
                "procedure_step": "task-action",
                "controls": [
                    {"id": target, "role": "text", "text_ids": [text_id], "confidence": 1}
                ],
                "steps": [
                    {
                        "target": target,
                        "action": "click",
                        "intent": "navigate",
                        "point": [100, 100],
                    }
                ],
            }
        )

    with pytest.raises(ValueError, match="saved_row_target_mismatch"):
        validate_match_plan(planned(12, 3), capability, request, services, set(brain.values))
    accepted = validate_match_plan(planned(30, 4), capability, request, services, set(brain.values))
    assert accepted.steps[0].target == 30


def test_low_confidence_action_target_is_rejected_before_execution(
    tmp_path, record, task_instruction
):
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    screen = {"ocr": [], "markers": [{"id": 6, "source": "omniparser", "text_ids": []}]}
    request, services = matching_contract(brain, screen)
    response = ScreenMatch.model_validate(
        {
            "screen_kind": "menu",
            "taught_step_id": "open-query",
            "procedure_step": "task-action",
            "steps": [{"target": 6, "action": "click", "intent": "navigate", "point": [10, 10]}],
            "controls": [{"id": 6, "role": "button", "confidence": 0.6}],
        }
    )
    capability = ActionCapability(
        actions=["navigate"], goal="Open query", completion="Query is visible"
    )

    with pytest.raises(ValueError, match="action_target_uncertain"):
        validate_match_plan(response, capability, request, services, set(brain.values))
    event = brain.log.read_all()[-1]
    assert event.payload["validator"] == "context_recall.validate_match_plan"
    rejected = json.loads(Path(event.payload["artifact"]).read_text(encoding="utf-8"))
    assert rejected["response"] == response.model_dump(mode="json")
    assert rejected["reason"] == "screen_match_action_target_uncertain"


def test_taught_direct_input_is_not_written_into_a_detected_grid_cell(tmp_path, record):
    task_instruction = TaskInstruction.model_validate(
        {
            "name": "Direct fields only",
            "objective": "Enter direct record fields without line items.",
            "completion_conditions": ["Record fields are entered."],
            "procedure": [
                {
                    "id": "fill-record",
                    "instruction": "Enter the record fields.",
                    "completion": "The direct fields match.",
                    "actions": [
                        {"verb": "fill", "target": "House reference", "input_key": "hbl_no"},
                        {"verb": "fill", "target": "Shipper", "input_key": "shipper"},
                    ],
                }
            ],
        }
    )
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    proposed = ScreenMatch.model_validate(
        {
            "screen_kind": "editor",
            "procedure_step": "task-action",
            "controls": [
                {"id": 3, "role": "cell", "confidence": 1},
                {"id": 5, "role": "edit", "confidence": 1},
            ],
            "steps": [
                {"target": 5, "action": "fill", "intent": "fill", "source": "hbl_no"},
                {"target": 3, "action": "fill", "intent": "fill", "source": "shipper"},
            ],
        }
    )

    request, services = matching_contract(brain)
    filtered = enforce_taught_input_surfaces(request, services, proposed)

    assert [(step.target, step.source) for step in filtered.steps] == [(5, "hbl_no")]
    event = next(
        item
        for item in brain.log.read_all()
        if item.payload.get("check") == "taught_input_surface_mismatch_deferred"
    )
    assert event.payload["rejected"] == [
        {
            "source": "shipper",
            "target": 3,
            "observed_surface": "table_cell",
            "allowed_surfaces": ["direct_input"],
        }
    ]


@pytest.mark.parametrize(
    "role,parent_role,surface,allowed",
    [
        ("cell", None, "table_cell", True),
        ("edit", "row", "table_cell", True),
        ("combobox", "table", "any", True),
        ("edit", None, "table_cell", False),
        ("edit", "row", None, False),
        ("edit", None, None, True),
    ],
)
def test_taught_surface_agrees_through_compile_dispatch_and_reread(
    tmp_path, record, role, parent_role, surface, allowed
):
    task_instruction = TaskInstruction.model_validate(
        {
            "name": "Line item entry",
            "objective": "Enter one line item.",
            "completion_conditions": ["The line item is entered."],
            "procedure": [
                {
                    "id": "fill-line",
                    "instruction": "Enter the line quantity.",
                    "completion": "The grid quantity matches.",
                    "actions": [
                        {
                            "verb": "fill",
                            "target": "Line quantity cell",
                            "input_key": "packages.count",
                            "surface": surface,
                        }
                    ],
                }
            ],
        }
    )
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    screen = {
        "markers": [
            {"id": 1, "bbox": [10, 10, 80, 30], "source": "omniparser"},
            {"id": 2, "bbox": [90, 10, 80, 30], "source": "omniparser"},
            {"id": 3, "bbox": [10, 40, 80, 30], "source": "omniparser"},
            {"id": 4, "bbox": [90, 40, 80, 30], "source": "omniparser"},
        ]
    }
    proposed = ScreenMatch.model_validate(
        {
            "screen_kind": "editor",
            "procedure_step": "task-action",
            "controls": [{"id": 3, "role": role, "confidence": 1}],
            "steps": [
                {
                    "target": 3,
                    "action": "fill",
                    "intent": "fill",
                    "source": "packages.count",
                }
            ],
        }
    )

    from sawon_interfaces import ActionResult

    from brain.graph.execution import GuardStop
    from brain.graph.screen_batch import ScreenFillPlan, prepare_screen_fill
    from brain.visual.markers import Marker, ground_meanings
    from brain.visual.scene import ControlMeaning, bind_scene
    from pipeline.schemas import OcrBlock

    if parent_role is not None:
        screen["markers"].append({"id": 6, "bbox": [0, 0, 180, 80], "source": "omniparser"})
        proposed.controls[0] = proposed.controls[0].model_copy(update={"parent_id": 6})
        proposed.controls.append(ControlMeaning(id=6, role=parent_role, confidence=1))
    markers = [Marker(**marker, confidence=1) for marker in screen["markers"]]
    brain.observation = bind_scene(wire(), [], ground_meanings(proposed, markers, []))
    plan = prepare_screen_fill(
        brain.observation, compile_match(proposed, brain.observation, ScreenFillPlan)
    )
    decision = plan.actions[0].decision
    request, services = matching_contract(brain, screen)
    if not allowed:
        with pytest.raises(ValueError, match="task_surface_mismatch"):
            enforce_taught_input_surfaces(request, services, proposed)
        with pytest.raises(GuardStop, match="record_input_scope_mismatch"):
            brain._guard_action(decision)
        return
    assert enforce_taught_input_surfaces(request, services, proposed).steps == proposed.steps
    sent = []

    def act(action):
        sent.append(action)
        return ActionResult(
            action_id=action.root.action_id,
            ok=True,
            duration_ms=0,
            post_obs_id=brain.relay.current.obs_id,
        )

    brain.relay.act = act
    assert brain._execute_plan(plan) == "observation", brain.feedback
    assert brain.pending is not None
    assert len(sent) == 1 and sent[0].root.text == brain.values["packages.count"]
    blocks = [
        OcrBlock(id=10, text=brain.values["packages.count"], bbox=(15, 45, 50, 15), confidence=1)
    ]
    proposed.controls[0] = proposed.controls[0].model_copy(update={"text_ids": [10]})
    after = bind_scene(wire(), blocks, ground_meanings(proposed, markers, blocks))
    brain.screen_scanner.scan = lambda _: after
    assert brain._verify_node() == "review"
    assert "packages.count" in brain.verified


def test_model_only_generic_task_keeps_model_grounded_input_progress(
    tmp_path, record, task_instruction
):
    task_response = named_response(procedure_step="Enter the visible task input")
    task_response["steps"] = []
    task_response["operation"] = "stop"
    task_response["evidence"] = [{"field": "hbl_no", "target": "Reference"}]
    task_response["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    brain, *_ = setup_brain(tmp_path, record, [task_response], task_instruction=task_instruction)
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})

    brain._observe()
    assert brain._select_node() == "finish"

    assert "hbl_no" in brain.verified
    event = next(
        event
        for event in brain.log.read_all()
        if event.payload.get("check") == "model_task_evidence_recorded"
    )
    assert event.payload["fields"] == ["hbl_no"]


def test_post_save_model_review_uses_only_persisted_detail_evidence(
    tmp_path, record, task_instruction
):
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    brain.verified = {field: {"pre_save": field} for field in brain.values}
    brain.model_saved_detail_review = True
    brain.post_save_verified = {"hbl_no"}

    request, services = matching_contract(brain)
    prepared = build_context_prompt(request, services)
    notebook = prepared.state["notebook"]

    assert request.verified_fields == {"hbl_no"}
    assert notebook["verified_inputs"] == ["hbl_no"]
    assert set(notebook["remaining_inputs"]) == set(brain.values) - {"hbl_no"}
    assert notebook["workflow_state"]["post_save_detail_review"] is True


def test_post_save_query_input_is_not_mistaken_for_a_saved_result_row(
    tmp_path, record, task_instruction
):
    task_response = named_response()
    task_response.update(
        {
            "steps": [],
            "operation": "stop",
            "evidence": [{"field": "hbl_no", "target": "Reference"}],
        }
    )
    brain, *_ = setup_brain(tmp_path, record, [task_response], task_instruction=task_instruction)
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    brain.save_issued = True

    brain._observe()

    assert not brain.post_save_query_identity_verified


@pytest.mark.parametrize(
    "role, action_type, intent",
    [
        ("cell", "double_click", "navigate"),
        ("button", "click", "navigate"),
        ("cell", "key", "navigate"),
        ("cell", "click", "search"),
    ],
    ids=["row", "edit-button", "keyboard", "search-intent"],
)
def test_post_save_result_transition_starts_detail_review_and_advances_taught_step(
    tmp_path, record, role, action_type, intent
):
    from sawon_interfaces import UIElement

    from brain.graph.schemas import Choice, Decision
    from brain.graph.transitions import ObservedEffect

    taught = TaskInstruction.model_validate_json(
        (
            Path(__file__).resolve().parents[1] / "examples/shipment-user-taught-task.json"
        ).read_bytes()
    )
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=taught)
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    brain.save_issued = True
    brain.active_taught_step = "requery-saved-record"
    identity = brain.values["hbl_no"]
    result = UIElement(
        ref="e1",
        role=role,
        name="Open selection" if role == "button" else identity,
        value="" if role == "button" else identity,
        enabled=True,
        bbox=(100, 50, 150, 30),
    )
    query = SceneObservation(wire=wire(), screen_kind="query", elements=[result])
    decision = Decision(
        screen_kind="query",
        operation="act",
        intent=intent,
        subgoal="open saved result",
        reason="review the persisted values",
        action=Choice(type=action_type, ref="e1", keys="enter" if action_type == "key" else None),
    )
    brain.observation = query
    action = brain._wire_action(decision.action, decision)
    brain._accept_dispatched_action(
        action,
        decision,
        decision.action,
        progress_scope="task:requery-saved-record:0",
        point_refined=False,
    )
    assert brain.saved_detail_transition_pending is True

    # A row selection that stays on the query screen is not a completed transition.
    brain.observation = SceneObservation(wire=wire(), screen_kind="query", elements=[result])
    brain._record_action_verification(
        decision, decision.action, None, True, ObservedEffect.CHANGED, []
    )
    assert not brain.model_saved_detail_review

    # A modal (or a model-labelled dialog) and its next action cannot erase the
    # query-to-detail episode. Its original taught step must also survive.
    brain.pending = None
    brain._begin_saved_detail_review("dialog")
    assert brain.saved_detail_transition_pending
    brain.observation = SceneObservation(wire=wire(), screen_kind="dialog", elements=[result])
    decision = decision.model_copy(update={"screen_kind": "dialog", "intent": "navigate"})
    brain.active_taught_step = "verify-requeried-record"
    action = brain._wire_action(decision.action, decision)
    brain._accept_dispatched_action(
        action,
        decision,
        decision.action,
        progress_scope="task:requery-saved-record:1",
        point_refined=False,
    )
    brain.observation = SceneObservation(wire=wire(), screen_kind="editor", elements=[])
    brain._record_action_verification(
        decision, decision.action, None, True, ObservedEffect.CHANGED, []
    )
    assert not brain.saved_detail_transition_pending
    assert brain.saved_detail_transition_step is None

    assert brain.post_save_query_identity_verified
    assert brain.model_saved_detail_review
    assert brain.active_taught_step == "verify-requeried-record"
    assert "requery-saved-record" in brain.completed_taught_steps
    assert any(
        event.payload.get("check") == "model_saved_detail_review_started"
        for event in brain.log.read_all()
    )


def test_complete_post_save_model_evidence_finishes_before_another_decision(
    tmp_path, record, task_instruction
):
    brain, model, relay, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    brain.save_issued = True
    brain.post_save_query_identity_verified = True
    brain.model_saved_detail_review = True
    brain.post_save_verified = set(brain.values)

    assert brain._select_node() == "finish"

    assert brain.result is not None and brain.result.status == "success"
    assert brain.result.reason == "task_completed_with_verified_evidence"
    assert model.prompts == []
    assert not hasattr(relay, "actions")
    event = next(
        event
        for event in brain.log.read_all()
        if event.payload.get("check") == "post_save_model_evidence_complete"
    )
    assert event.payload["fields"] == sorted(brain.values)


def test_post_save_exploration_failures_are_scoped_by_review_stage(
    tmp_path, record, task_instruction
):
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    brain.save_issued = True

    assert brain._exploration_goal_key() == "post_save:requery_identity"
    brain.post_save_query_identity_verified = True
    assert brain._exploration_goal_key() == "post_save:open_detail"
    brain.model_saved_detail_review = True
    brain.post_save_verified = {"hbl_no"}
    assert brain._exploration_goal_key() == "post_save_detail:" + ",".join(
        sorted(set(brain.values) - {"hbl_no"})
    )


def test_selection_error_does_not_consume_unrelated_phase_failure_budget(
    tmp_path, record, task_instruction
):
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    brain.phase = "task"
    brain.phase_failures["task"] = 1

    assert brain._fail("selection_error:KeyError") == "observation"
    assert brain.phase_failures["task"] == 1

    assert brain._fail("selection_error:KeyError") == "finish"
    assert brain.result is not None
    assert brain.result.reason == "repeated_subgoal_failure:task:decision:selection_error:KeyError"


def test_semantic_target_failure_uses_exploration_action_scope(tmp_path, record, task_instruction):
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    brain.phase = "task"
    brain._last_exploration_failure_key = "click:additional-tab"

    assert brain._fail("semantic_target_repeated") == "observation"

    assert brain.feedback[-1]["subgoal_key"] == "task:explore:click:additional-tab"
    assert brain.phase_failures["task"] == 0


def test_post_save_completion_rejection_names_missing_evidence_and_scope(
    tmp_path, record, task_instruction
):
    from brain.graph.schemas import Decision

    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    brain.observation = SceneObservation(wire=wire(), screen_kind="editor", elements=[])
    brain.save_issued = True
    brain.post_save_query_identity_verified = True
    brain.model_saved_detail_review = True
    brain.post_save_verified = set(brain.values) - {"description"}
    decision = Decision(
        screen_kind="editor",
        operation="complete",
        intent="verify",
        subgoal="verify persisted values",
        reason="visible persisted values match",
    )
    brain.decision = decision

    assert brain._check_complete(decision) == "observation"

    feedback = brain._current_feedback()[-1]
    assert feedback["missing_fields"] == ["description"]
    assert "Do not refill or save" in feedback["required_response"]
    assert feedback["subgoal_key"] == "task:post_save_detail:description"
    event = brain.log.read_all()[-1]
    assert event.payload["subgoal_key"] == "task:post_save_detail:description"

    request, services = matching_contract(brain)
    prepared = build_context_prompt(request, services)
    prompted = prepared.state["notebook"]["recent_feedback"][-1]
    assert prompted["missing_fields"] == ["description"]
    assert prompted["subgoal_key"] == "task:post_save_detail:description"
    assert "Do not refill or save" in prompted["required_response"]


def test_education_focus_can_revisit_steps_without_inventing_completion(tmp_path, record):
    taught = TaskInstruction.model_validate_json(
        (
            Path(__file__).resolve().parents[1] / "examples/shipment-user-taught-task.json"
        ).read_bytes()
    )
    brain, *_ = setup_brain(tmp_path, record, [], task_instruction=taught)

    initial = brain._taught_procedure_progress()
    assert [step["id"] for step in initial["steps"]] == ["login", "open-query"]
    assert initial["completed_step_ids"] == []

    brain._advance_taught_procedure("save-record")
    advanced = brain._taught_procedure_progress()
    assert [step["id"] for step in advanced["steps"]] == [
        "save-record",
        "requery-saved-record",
    ]
    assert advanced["completed_step_ids"] == []
    assert [step["id"] for step in initial["index"]] == [step.id for step in taught.procedure]

    brain._advance_taught_procedure("check-duplicate")
    assert brain._taught_procedure_progress()["focused_step_id"] == "check-duplicate"
    assert brain._taught_procedure_progress()["completed_step_ids"] == []
    brain._complete_taught_procedure_step("save-record")
    assert brain._taught_procedure_progress()["completed_step_ids"] == ["save-record"]


def test_model_only_review_retains_named_text_evidence_without_input_detection(
    tmp_path, record, task_instruction
):
    data = named_response(
        screen_kind="editor",
        operation="stop",
        steps=[],
        fields={
            "Reference": {
                "role": "text",
                "bbox": [10, 20, 60, 15],
                "value": record.fields.hbl_no.value,
            }
        },
        evidence=[{"field": "hbl_no", "target": "Reference"}],
    )
    brain, model, *_ = setup_brain(tmp_path, record, [data], task_instruction=task_instruction)
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    brain._observe()
    assert "hbl_no" in brain.verified
    assert len(model.prompts) == 1


@pytest.mark.parametrize("visible", ["", "WRONG"], ids=["blank", "different"])
def test_observed_mismatch_replaces_claim_without_rejecting_repair(
    tmp_path, record, task_instruction, visible
):
    task_response = named_response()
    task_response["fields"]["Reference"]["value"] = visible
    task_response["evidence"] = [{"field": "hbl_no", "target": "Reference"}]
    brain, *_ = setup_brain(tmp_path, record, [task_response], task_instruction=task_instruction)
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    brain.verified["hbl_no"] = {"old": "entry"}
    brain._observe()
    assert "hbl_no" not in brain.verified
    assert brain.visual_proposal.evidence == []
    assert brain._select_node() == "review"
    assert brain.action_plan.actions[0].decision.action.source == "hbl_no"


def test_task_capability_allows_save_after_prior_evidence_without_hiding_inputs(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "task"
    active = {"description", "hbl_no", "shipper"}
    brain.verified = {field: f"prior-{field}" for field in active}
    policy = DecisionPolicy(
        ExecutionFacts(
            "task",
            field_names=("description", "hbl_no", "shipper"),
            verified_field_names=("description", "hbl_no", "shipper"),
        )
    )
    contract = {
        "allowed_intents": [
            choice["intent"] for choice in policy.as_prompt()["allowed_next_choices"]
        ],
        "checkpoint": policy.current_step,
    }
    capability = task_action_contract(brain.phase, brain.task_instruction, contract, active)
    response = ScreenMatch.model_validate(
        {
            "screen_kind": "editor",
            "procedure_step": "task-action",
            "controls": [
                {"id": 24, "role": "edit", "confidence": 1},
                {"id": 43, "role": "button", "confidence": 1},
            ],
            "steps": [
                {
                    "target": 43,
                    "action": "click",
                    "intent": "save",
                    "point": [700, 50],
                }
            ],
            "evidence": [],
        }
    )

    request, services = matching_contract(brain)
    validated = validate_match_plan(response, capability, request, services, set(brain.values))
    assert validated.steps[0].intent == "save"
    assert "fill" in capability.actions
    assert "save" in capability.actions


def test_ocr_role_mismatch_keeps_requested_image_and_refinement(tmp_path, record):
    class BrainStub:
        log = RunLog(tmp_path, case_id="ocr-role-mismatch", agent_id="test")

    response = ScreenMatch.model_validate(
        {
            "screen_kind": "editor",
            "procedure_step": "task-action",
            "controls": [{"id": 5, "role": "window", "confidence": 0.95}],
            "steps": [
                {
                    "target": 5,
                    "action": "key",
                    "intent": "navigate",
                    "keys": "pagedown",
                }
            ],
            "needs_image": True,
            "needs_refinement": True,
        }
    )
    screen = {"markers": [{"id": 5, "source": "ocr"}]}

    assert omit_nonblocking_image_request(BrainStub(), response, screen).needs_image
    assert omit_nonblocking_refinement(BrainStub(), response, screen).needs_refinement
    checks = {event.payload.get("check") for event in BrainStub.log.read_all()}
    assert "nonblocking_image_request_omitted" not in checks
    assert "nonblocking_refinement_omitted" not in checks


def test_generic_task_discards_prose_source_from_non_binding_click(
    tmp_path, record, task_instruction
):
    task_response = named_response(procedure_step="Open the relevant work area")
    task_response["steps"][0].update(
        action="click",
        intent="navigate",
        source="House B/L work menu",
        point=[175, 30],
    )
    brain, *_ = setup_brain(tmp_path, record, [task_response], task_instruction=task_instruction)

    brain._observe()

    assert brain.visual_proposal.procedure_step == "task-action"
    assert brain.visual_proposal.steps[0].intent == "navigate"
    assert brain.visual_proposal.steps[0].source is None


@pytest.mark.parametrize(
    "action, source", [("key", None), ("click", None), ("double_click", "hbl_no")]
)
def test_nonwriting_input_goal_preserves_literal_action_without_replanning(
    tmp_path, record, task_instruction, action, source
):
    task_response = named_response(procedure_step="Expose the next task input")
    task_response["fields"]["Reference"]["role"] = (
        "cell" if action == "double_click" else "text" if action == "click" else "edit"
    )
    task_response["steps"][0].update(
        action=action,
        intent="fill",
        source=source,
        keys="tab" if action == "key" else None,
        point=[175, 30],
    )
    task_response["evidence"] = [{"field": "hbl_no", "target": "Reference"}]
    brain, model, *_ = setup_brain(
        tmp_path, record, [task_response], task_instruction=task_instruction
    )

    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})
    brain._observe()

    assert brain.visual_proposal.steps[0].intent == "navigate"
    assert brain._select_node() == "review"
    assert brain.decision.intent == "navigate"
    assert brain.decision.action.type == action
    assert len(model.prompts) == 1
    assert brain.decision.action.source is None
    assert brain.decision.action.keys == ("tab" if action == "key" else None)
    event = next(
        event
        for event in brain.log.read_all()
        if event.payload.get("check") == "nonwriting_fill_intent_normalized"
    )
    assert event.payload["intent"] == "navigate"


def test_promoted_exact_visual_match_reuse_precedes_model_call(tmp_path, record, task_instruction):
    cache_path = tmp_path / "visual-match.json"

    first, first_model, *_ = setup_brain(
        tmp_path / "first", record, [named_response()], task_instruction=task_instruction
    )
    first.visual_match_cache = ScreenPatternCache(cache_path, enabled=True)
    first._observe()
    assert len(first_model.prompts) == 1
    assert first.visual_match_candidate is not None
    first.visual_match_cache.stage(*first.visual_match_candidate)
    assert first.visual_match_cache.finish(success=True) == 1

    second, second_model, *_ = setup_brain(
        tmp_path / "second", record, [], task_instruction=task_instruction
    )
    second.visual_match_cache = ScreenPatternCache(cache_path, enabled=True)
    second._observe()
    assert second_model.prompts == []
    assert second._select_node() == "review"
    assert second_model.prompts == []
    hits = [
        event
        for event in second.log.read_all()
        if event.kind == "cache_hit" and event.payload.get("check") == "visual_match_cache"
    ]
    assert len(hits) == 1


@pytest.mark.parametrize("has_review", [False, True], ids=["action-only", "current-save-review"])
def test_save_result_reuses_bound_review_or_falls_back_to_current_review_cache(
    tmp_path, record, task_instruction, has_review
):
    brain, model, *_ = setup_brain(tmp_path, record, [], task_instruction=task_instruction)
    request, services = matching_contract(brain)
    request = replace(request, phase="save_result")
    brain.memory_retrieval.enabled = True

    bound = []

    def bind_method(screen):
        bound.append(screen)
        return ScreenMatch(
            screen_kind="dialog",
            procedure_step="task-action",
            controls=[
                {"id": 7, "role": "button", "confidence": 1},
                {"id": 8, "role": "text", "confidence": 1},
            ],
            steps=[{"target": 7, "action": "click", "intent": "navigate", "point": [20, 20]}],
            save_result="ready_for_query" if has_review else None,
            message_targets=[8] if has_review else [],
        )

    services.memory_retrieval.retrieve = bind_method
    cache = ScreenPatternCache(tmp_path / "save-result-cache.json", enabled=True)
    services = replace(services, cache=cache)
    prepared = build_context_prompt(request, services)
    cached = ScreenMatch(
        screen_kind="dialog",
        controls=[],
        procedure_step="inspect-save",
        operation="stop",
        expected_result="Inspect the current save result",
    )
    cache.stage(screen_pattern_key(prepared.state), cached.model_dump(mode="json"))
    assert cache.finish(success=True) == 1

    selected = select_context_response(request, services, prepared)

    if has_review:
        assert selected.response == selected.method_proposal
        assert selected.response.save_result == "ready_for_query"
        assert selected.response.steps[0].intent == "navigate"
        assert selected.cache_hit is None
        assert not selected.used_image
    else:
        assert selected.response == cached
        assert selected.cache_hit is not None
    assert bound == [request.screen]
    assert model.prompts == []


def test_identical_capture_reuses_ocr_but_replans_current_task_action(tmp_path, record):
    next_action = named_response(
        fields={"Next": {"role": "button", "bbox": [100, 15, 150, 30]}},
        assignments={},
        steps=[{"target": "Next", "action": "click", "intent": "navigate", "point": [175, 30]}],
    )
    brain, model, relay, ocr, detector, _ = setup_brain(
        tmp_path, record, [named_response(), next_action, next_action]
    )
    brain._observe()
    first_id = brain.observation.obs_id
    brain._observe()
    assert brain.observation.obs_id != first_id
    assert ocr.calls == detector.calls == len(model.prompts) == 1
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert len(model.prompts) == 2
    assert brain.decision.intent == "navigate"
    assert brain._wire_action(brain.decision.action, brain.decision).root.target.image_point == (
        175,
        30,
    )
    relay.color = "black"
    brain._observe()
    assert ocr.calls == detector.calls == 2


def test_gemini_interprets_and_plans_in_one_image_call(tmp_path, record, task_instruction):
    plan = named_response()
    plan["evidence"] = [{"field": "hbl_no", "target": "Reference"}]
    plan["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    brain, model, _, _, _, _ = setup_brain(
        tmp_path, record, [plan], task_instruction=task_instruction
    )
    images_seen = []
    original = model._generate

    def generate(prompt, schema, *, images=None):
        images_seen.append(bool(images))
        return original(prompt, schema, images=images)

    model._generate = generate
    brain._observe()
    assert brain.saved_bindings == (
        str(brain.observation.obs_id),
        {"hbl_no": brain.observation.elements[0].ref},
    )
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert images_seen == [True]
    assert model.prompts[0].startswith(CONTEXT_MAPPING_RULES)


def test_task_planner_sees_that_post_save_query_is_still_missing(
    tmp_path, record, task_instruction
):
    task_response = named_response(procedure_step="Continue the taught procedure")
    brain, model, *_ = setup_brain(
        tmp_path, record, [task_response], task_instruction=task_instruction
    )
    brain.saved_evidence.begin("saved-dialog-observation")

    brain._observe()

    state = json.loads(model.prompts[0][len(CONTEXT_MAPPING_RULES) :])
    saved = state["notebook"]["saved_record_review"]
    assert saved["query_row_seen"] is False
    assert saved["confirmed_fields"] == []
    assert saved["missing_fields"] == sorted(brain.values)


def test_identical_rematch_reuses_grounded_interpretation(tmp_path, record, task_instruction):
    from brain.observe.progress import navigation_state

    full = named_response()
    full["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    brain, model, _, ocr, detector, _ = setup_brain(
        tmp_path, record, [full, full], task_instruction=task_instruction
    )
    brain._observe()
    before = brain.observation
    brain._observe()
    assert brain.observation.obs_id != before.obs_id
    assert navigation_state(brain.observation) == navigation_state(before)
    assert [(e.name, e.value) for e in brain.observation.elements] == [
        (e.name, e.value) for e in before.elements
    ]
    assert len(model.prompts) == 1
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert len(model.prompts) == 2
    assert model.prompts[-1].startswith(CONTEXT_MAPPING_RULES)
    assert ocr.calls == detector.calls == 1
    assert any(
        event.payload.get("check") == "identical_form_reused" for event in brain.log.read_all()
    )


def test_task_partner_input_compiles_to_current_screen_batch(tmp_path, record):
    fixture()[-1]
    partner = named_response(
        screen_kind="partner",
        procedure_step="lookup-partner",
        steps=[
            {
                "target": "Reference",
                "point": [175, 30],
                "action": "fill",
                "source": "shipper",
                "intent": "fill",
            }
        ],
    )
    brain, _, _, _, _, _ = setup_brain(tmp_path, record, [partner])
    brain.duplicate_clear = True
    brain.verified = {
        field: f"ref-{field}"
        for field in {"hbl_no", "mbl_no", "packages.count", "packages.unit", "pol"}
    }

    brain._observe()

    assert brain.observation.screen_kind == "partner"
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert len(brain.action_plan.actions) == 1
    assert brain.action_plan.actions[0].decision.intent == "fill"
    assert brain.action_plan.actions[0].decision.action.source == "shipper"


def test_observe_only_prefers_model_completion_over_an_attached_action(tmp_path, record):
    fixture()[-1]
    data = named_response(
        operation="complete",
        completion_evidence=[{"condition_index": 0, "target": "Reference"}],
    )
    data["steps"][0]["point"] = [9999, 9999]
    brain, *_ = setup_brain(tmp_path, record, [data])
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})

    brain._observe()

    assert brain._select_node() == "review"
    assert brain.decision.operation == "complete"
    assert brain.decision.action is None
    event = next(
        event
        for event in brain.log.read_all()
        if event.payload.get("guard") == "proof_with_actions"
    )
    assert event.payload["accepted_operation"] == "complete"
    assert event.payload["ignored_actions"] == 1


def test_observe_only_retains_a_task_action_attached_to_duplicate_evidence(tmp_path, record):
    data = named_response(operation="duplicate_clear")
    brain, *_ = setup_brain(tmp_path, record, [data])
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})

    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"

    retained = brain.action_plan.actions[0].decision
    assert retained.operation == "act"
    assert retained.action is not None
    event = next(
        event
        for event in brain.log.read_all()
        if event.payload.get("guard") == "proof_with_actions"
    )
    assert event.payload == {
        "check": "compound_response_normalized",
        "guard": "proof_with_actions",
        "reported_operation": "duplicate_clear",
        "outcome": "normalized_to_action",
        "retained_actions": 1,
    }


def test_semantic_action_requires_model_selected_point_before_execution(tmp_path, record):
    from pydantic import ValidationError

    wrong = named_response()
    wrong["steps"][0].pop("point")
    brain, model, *_ = setup_brain(tmp_path, record, [wrong])
    with pytest.raises(ValidationError, match="point"):
        brain._observe()
    assert len(model.prompts) == 1
    assert not any(event.kind == "act" for event in brain.log.read_all())


def test_stop_is_rejected_when_current_review_has_all_required_evidence(tmp_path, record):
    meaning = fixture()[-1]
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})
    request, services = matching_contract(brain)
    request = replace(
        request,
        policy=DecisionPolicy(replace(request.policy.facts, final_evidence_ready=True)),
    )
    proposal = ScreenMatch.model_validate(
        response(meaning, operation="stop", steps=[], procedure_step="task-action")
    )

    with pytest.raises(ValueError, match="screen_match_completion_decision_required"):
        validate_match_plan(
            proposal,
            task_action_contract(
                brain.phase,
                brain.task_instruction,
                request.execution_contract,
                set(brain.values),
            ),
            request,
            services,
            set(brain.values),
        )
    event = brain.log.read_all()[-1]
    rejected = json.loads(Path(event.payload["artifact"]).read_text(encoding="utf-8"))
    assert rejected["response"] == proposal.model_dump(mode="json")
    assert rejected["reason"] == "screen_match_completion_decision_required"


def test_model_may_complete_from_current_evidence_before_the_last_taught_step(tmp_path, record):
    fixture()[-1]
    instruction = TaskInstruction.model_validate(
        {
            "name": "Taught task",
            "objective": "Follow the taught route and verify the final screen.",
            "completion_conditions": ["The final screen contains the supplied value."],
            "procedure": [
                {
                    "id": "open-detail",
                    "instruction": "Open the matching result.",
                    "completion": "The detail screen is open.",
                    "actions": [{"verb": "activate", "target": "matching result"}],
                },
                {
                    "id": "verify-detail",
                    "instruction": "Verify the detail values.",
                    "completion": "All supplied values match.",
                    "actions": [{"verb": "verify", "target": "detail values"}],
                },
            ],
        }
    )
    premature = named_response(
        operation="complete",
        steps=[],
        taught_step_id="open-detail",
        completion_evidence=[{"condition_index": 0, "target": "Reference"}],
    )
    brain, model, _, ocr, detector, _ = setup_brain(
        tmp_path,
        record,
        [premature],
        task_instruction=instruction,
    )

    brain._observe()

    assert len(model.prompts) == 1
    assert ocr.calls == detector.calls == 1
    assert brain.visual_proposal.operation == "complete"
    assert brain.visual_proposal.taught_step_id == "open-detail"


def test_login_and_record_arrays_compile_to_existing_batches(tmp_path, record):
    from test_brain import element

    observed = wire()
    prefix = "e" + str(observed.obs_id.int) + "0"
    scene = SceneObservation(
        wire=observed,
        screen_kind="login",
        elements=[element(prefix + str(i), "Edit" if i < 3 else "Button") for i in (1, 2, 3)],
    )
    login = ScreenMatch(
        screen_kind="login",
        controls=[],
        procedure_step="login",
        steps=[
            {
                "target": 1,
                "action": "fill",
                "source": "credential.username",
                "intent": "credential",
            },
            {
                "target": 2,
                "action": "fill",
                "source": "credential.password",
                "intent": "credential",
            },
            {"target": 3, "action": "click", "intent": "login", "point": [1, 1]},
        ],
    )
    plan = compile_match(login, scene, LoginScreenPlan)
    assert len(plan.fields) == 2 and plan.login_ref == prefix + "3"
    with pytest.raises(ValueError, match="invalid_login_batch"):
        compile_match(login.model_copy(update={"steps": login.steps[:-1]}), scene, LoginScreenPlan)
    fields = ScreenMatch(
        screen_kind="editor",
        controls=[],
        procedure_step="fill-entry",
        steps=[
            {"target": 1, "action": "fill", "source": "hbl_no", "intent": "fill"},
            {"target": 2, "action": "fill", "source": "mbl_no", "intent": "fill"},
        ],
    )
    assert len(compile_match(fields, scene, ScreenFillPlan).fields) == 2
    # Input data is reusable: two distinct fields may need the same source.
    repeated_value = fields.model_copy(
        update={"steps": [fields.steps[0], fields.steps[0].model_copy(update={"target": 2})]}
    )
    repeated_plan = compile_match(repeated_value, scene, ScreenFillPlan)
    assert [item.source for item in repeated_plan.fields] == ["hbl_no", "hbl_no"]
    assert len({item.ref for item in repeated_plan.fields}) == 2
    query_input = ScreenMatch(
        screen_kind="query",
        controls=[],
        procedure_step="task-action",
        steps=[
            {
                "target": 1,
                "action": "fill",
                "source": "hbl_no",
                "intent": "query_input",
            }
        ],
    )
    query_decision = compile_match(query_input, scene, DecisionResponse).to_decision()
    assert query_decision.intent == "query_input"
    assert query_decision.action is not None
    assert query_decision.action.ref == prefix + "1"
    stop = ScreenMatch(
        screen_kind="query", controls=[], procedure_step="check-duplicate", operation="stop"
    )
    assert compile_match(stop, scene, DecisionResponse).to_decision().operation == "stop"
    # Provider key names are case-insensitive at the screen-matching boundary;
    # the executor receives its canonical wire spelling.
    scroll = ScreenMatch(
        screen_kind="editor",
        controls=[],
        procedure_step="show-more-fields",
        steps=[
            {
                "target": 3,
                "action": "key",
                "intent": "navigate",
                "keys": "PAGEDOWN",
            }
        ],
    )
    scroll_decision = compile_match(scroll, scene, ScreenFillPlan).next_decision.to_decision()
    assert scroll_decision.action.keys == "pagedown"
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.observation = scene
    action = brain._wire_action(
        scroll_decision.action,
        scroll_decision,
        fresh_observation=observed,
        log_renewal=False,
    ).root
    assert action.type == "scroll"
    assert action.wheel_delta == -600
    assert action.target.image_point

    # A model-grounded table can be disabled as an activation target when its
    # row semantics are uncertain. Its current local geometry is still a safe
    # wheel anchor because no click is dispatched to the control.
    disabled_scroll_scene = scene.model_copy(
        update={
            "screen_kind": "editor",
            "elements": [
                item.model_copy(update={"role": "table", "enabled": False})
                if item.ref == prefix + "3"
                else item
                for item in scene.elements
            ],
        }
    )
    brain.observation = disabled_scroll_scene
    brain._guard_action(
        scroll_decision,
        DecisionPolicy(ExecutionFacts("enter", duplicate_clear=True)),
    )


@pytest.mark.parametrize(
    "intents",
    [("navigate", "navigate"), ("select_option", "navigate")],
)
def test_same_screen_navigation_clicks_compile_to_one_action_plan(intents):
    from test_brain import element

    observed = wire()
    prefix = "e" + str(observed.obs_id.int) + "0"
    scene = SceneObservation(
        wire=observed,
        screen_kind="query",
        elements=[element(prefix + "31", "Row"), element(prefix + "44", "Button")],
    )
    response = ScreenMatch(
        screen_kind="query",
        controls=[],
        procedure_step="verify-final",
        steps=[
            {
                "target": 31,
                "action": "click",
                "intent": intents[0],
                "point": [1, 1],
            },
            {
                "target": 44,
                "action": "click",
                "intent": intents[1],
                "point": [1, 1],
            },
        ],
    )

    plan = compile_screen_action_plan(response, scene)

    assert plan is not None
    assert tuple(item.decision.intent for item in plan.actions) == intents
    assert [item.decision.action.ref for item in plan.actions] == [prefix + "31", prefix + "44"]
    assert plan.audit == {
        "check": "same_screen_action_plan",
        "actions": 2,
    }


def test_save_result_and_proof_use_the_existing_contract():
    from test_brain import element

    from brain.graph.save_review import SaveReviewResponse

    observed = wire()
    prefix = "e" + str(observed.obs_id.int) + "0"
    scene = SceneObservation(
        wire=observed,
        screen_kind="dialog",
        elements=[
            element(prefix + "1", "Text", name="Confirm saving"),
            element(prefix + "2", "Button", name="Confirm"),
        ],
    )
    result = ScreenMatch(
        screen_kind="dialog",
        controls=[],
        procedure_step="inspect-save",
        save_result="confirmation_required",
        message_targets=[1],
        steps=[
            {
                "target": 2,
                "action": "click",
                "intent": "confirm_save",
                "point": [1, 1],
            }
        ],
    )
    review = compile_match(result, scene, SaveReviewResponse)
    assert review.evidence_refs == [prefix + "1"]
    assert review.next_decision.to_decision().intent == "confirm_save"
    proof = ScreenMatch(
        screen_kind="query",
        controls=[],
        procedure_step="check-duplicate",
        operation="duplicate_clear",
        grid=1,
    )
    assert compile_match(proof, scene, DecisionResponse).to_decision().grid_ref == prefix + "1"


@pytest.mark.parametrize(
    "mode",
    [
        "present",
        "missing",
        "still_missing",
        "changed_value",
        "renamed",
        "renamed_wrong_value",
        "resized",
        "resized_wrong_value",
    ],
)
def test_delivered_input_verification_omission_is_repaired_without_reusing_old_value(
    tmp_path, record, mode
):
    from brain.graph.context_recall import required_verification, retain_verification
    from brain.graph.execution import GuardStop
    from brain.graph.schemas import Choice, Decision
    from brain.observe.summary import describe
    from brain.visual.markers import Marker, build_markers, ground_meanings
    from brain.visual.scene import SemanticDetection, bind_scene
    from pipeline.schemas import OcrBlock

    blocks, boxes, _, _ = fixture()
    expected = record.fields.hbl_no.value
    blocks.append(OcrBlock(id=9, text=expected, bbox=(110, 20, 70, 15), confidence=0.99))
    boxes.append(Marker(id=9, bbox=(0, 70, 400, 100), source="omniparser", confidence=0.9))
    markers = build_markers(blocks, boxes, 400, 200)
    field_id = next(m.id for m in markers if m.source != "ocr" and m.bbox[1] == 15)
    table_id = next(m.id for m in markers if m.source != "ocr" and m.bbox[1] == 70)
    field = {"id": field_id, "role": "edit", "label_ids": [8], "text_ids": [9], "confidence": 0.95}
    table = {"id": table_id, "role": "table", "table_state": "empty", "confidence": 0.95}
    initial = SemanticDetection(screen_kind="query", controls=[field, table])
    before = bind_scene(wire(), blocks, ground_meanings(initial, markers, blocks))
    from brain.visual.marker_contract import model_controls

    supplemental = {
        "controls": []
        if mode == "still_missing"
        else model_controls(initial.controls[:1], {"markers": [m.model_dump() for m in markers]})
    }
    brain, model, _, _, _, _ = setup_brain(tmp_path, record, [supplemental])
    brain.observation = before
    target = next(e for e in before.elements if e.role == "edit")
    decision = Decision(
        screen_kind="query",
        operation="act",
        intent="fill",
        subgoal="enter identifier",
        reason="user input",
        action=Choice(type="set_text", ref=target.ref, source="hbl_no"),
    )
    brain.pending = {
        "decision": decision,
        "choice": decision.action,
        "descriptor": describe(target, before.elements),
        "before_observation": before,
    }
    required = required_verification(brain.pending)
    services = matching_contract(brain)[1]
    if mode in {"changed_value", "renamed_wrong_value", "resized_wrong_value"}:
        blocks[-1] = blocks[-1].model_copy(update={"text": "DIFFERENT-NOW"})
    if mode.startswith("renamed"):
        field = dict(field, label_ids=[])
    current_wire = wire()
    if mode.startswith("resized"):
        markers = [
            m.model_copy(update={"bbox": (100, 15, 500, 35)}) if m.id == field_id else m
            for m in markers
        ]
        current_wire = current_wire.model_copy(
            update={"screenshot": current_wire.screenshot.model_copy(update={"width": 800})}
        )
    screen = {
        "image_size": [current_wire.screenshot.width, current_wire.screenshot.height],
        "ocr": [b.model_dump() for b in blocks],
        "markers": [m.model_dump() for m in markers],
    }
    matched = ScreenMatch(
        screen_kind="query",
        procedure_step="task-action",
        controls=[table, field]
        if mode == "present" or mode.startswith(("renamed", "resized"))
        else [table],
        operation="stop",
        grid=table_id,
        evidence=[{"field": "hbl_no", "target": field_id}],
    )
    if mode == "still_missing":
        with pytest.raises(GuardStop, match="required_verification_missing"):
            retain_verification(services, matched, screen, required)
        assert len(model.prompts) == 1
        return
    repaired = retain_verification(services, matched, screen, required)
    assert len(model.prompts) == (
        0 if mode == "present" or mode.startswith(("renamed", "resized")) else 1
    )
    assert repaired.operation == matched.operation and repaired.steps == matched.steps
    brain.observation = bind_scene(current_wire, blocks, ground_meanings(repaired, markers, blocks))
    current_input = next(e for e in brain.observation.elements if e.role == "edit")
    accepted = brain._record_verified_field("hbl_no", current_input)
    assert accepted is (mode not in {"changed_value", "renamed_wrong_value", "resized_wrong_value"})
    assert ("hbl_no" in brain.verified) is accepted


def test_model_only_review_delegates_required_verification_without_repair_call(tmp_path, record):
    from brain.graph.context_recall import retain_verification

    brain, model, *_ = setup_brain(tmp_path, record, [])
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    matched = ScreenMatch(
        screen_kind="editor",
        procedure_step="task-action",
        operation="act",
        controls=[],
        steps=[],
    )
    required = [
        {
            "source": "hbl_no",
            "screen": "editor",
            "descriptor": {"role": "edit", "name": "HBL"},
        }
    ]

    assert (
        retain_verification(
            matching_contract(brain)[1], matched, {"ocr": [], "markers": []}, required
        )
        == matched
    )
    assert not model.prompts
    delegated = [
        event
        for event in brain.log.read_all()
        if event.payload.get("check") == "required_verification_delegated"
    ]
    assert delegated[-1].payload["reviewer"] == "screen_model"
    assert delegated[-1].payload["sources"] == ["hbl_no"]


def test_post_action_review_preserves_next_action_in_same_response(tmp_path, record):
    blocks, _, markers, meaning = fixture()
    reviewed = named_response()
    brain, model, *_ = setup_brain(tmp_path, record, [reviewed])
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})
    screen = {
        "ocr": [block.model_dump() for block in blocks],
        "markers": [marker.model_dump() for marker in markers],
    }
    request, services = matching_contract(brain, screen)

    result = recall_context(
        replace(request, post_action_review=True, pending_verification=True), services
    )

    assert len(model.prompts) == 1
    assert result.response.operation == "act"
    expected = ScreenMatch.model_validate(reviewed).steps[0]
    actual = result.response.steps[0]
    assert (actual.action, actual.source, actual.point) == (
        expected.action,
        expected.source,
        expected.point,
    )
    assert result.response.needs_refinement is False


def test_post_action_review_reuses_bound_next_method_without_model_call(tmp_path, record):
    blocks, _, markers, meaning = fixture()
    brain, model, *_ = setup_brain(tmp_path, record, [])
    screen = {
        "ocr": [block.model_dump() for block in blocks],
        "markers": [marker.model_dump() for marker in markers],
    }
    request, services = matching_contract(brain, screen)
    replay = ScreenMatch.model_validate(response(meaning))
    services.memory_retrieval.retrieve = lambda _screen: replay

    result = recall_context(
        replace(request, post_action_review=True, pending_verification=True), services
    )

    assert result.response.operation == replay.operation
    assert [(step.target, step.action, step.source) for step in result.response.steps] == [
        (step.target, step.action, step.source) for step in replay.steps
    ]
    assert model.prompts == []


def test_post_action_review_reuses_promoted_visual_decision_without_model_call(tmp_path, record):
    blocks, _, markers, meaning = fixture()
    brain, model, *_ = setup_brain(tmp_path, record, [])
    screen = {
        "ocr": [block.model_dump() for block in blocks],
        "markers": [marker.model_dump() for marker in markers],
    }
    request, services = matching_contract(brain, screen)
    request = replace(request, post_action_review=True, pending_verification=True)
    services.memory_retrieval.enabled = False
    services = replace(
        services,
        cache=ScreenPatternCache(tmp_path / "post-action-cache.json", enabled=True),
    )
    prepared = build_context_prompt(request, services)
    cached = ScreenMatch.model_validate(response(meaning))
    services.cache.stage(screen_pattern_key(prepared.state), cached.model_dump(mode="json"))
    assert services.cache.finish(success=True) == 1

    result = recall_context(request, services)

    assert [(step.target, step.action, step.source) for step in result.response.steps] == [
        (step.target, step.action, step.source) for step in cached.steps
    ]
    assert model.prompts == []
    assert any(
        event.kind == "cache_hit" and event.payload.get("check") == "visual_match_cache"
        for event in brain.log.read_all()
    )


def test_empty_action_recovery_preserves_education_and_current_observed_values(tmp_path, record):
    initial = named_response(steps=[])
    initial["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    recovered = named_response(
        fields={"Continue": {"role": "button", "bbox": [250, 15, 80, 30]}},
        assignments={},
        steps=[{"target": "Continue", "action": "click", "intent": "navigate", "point": [280, 30]}],
    )
    taught = TaskInstruction.model_validate_json(
        (
            Path(__file__).resolve().parents[1] / "examples/shipment-user-taught-task.json"
        ).read_bytes()
    )
    brain, model, *_ = setup_brain(tmp_path, record, [initial, recovered], task_instruction=taught)
    brain.settings = brain.settings.model_copy(
        update={"guard_mode": "observe_only", "visual_model_only_review": True}
    )
    brain._observe()
    assert len(model.prompts) == 2
    first, second = [json.loads(p[len(CONTEXT_MAPPING_RULES) :]) for p in model.prompts]
    assert second["task"] == first["task"]
    assert second["taught_procedure_progress"] == first["taught_procedure_progress"]
    assert second["recovery"]["matched_inputs"] == ["hbl_no"]
    assert "hbl_no" in brain.verified
    assert brain.visual_proposal.mapping.fields["Reference"].value == record.fields.hbl_no.value
    assert brain.visual_proposal.steps[0].intent == "navigate"
    assert brain.visual_proposal.mapping.steps[0].target == "Continue"


@pytest.mark.parametrize("operation", ["act", "stop"])
def test_empty_action_is_completed_once_or_explicitly_stopped(tmp_path, record, operation):
    initial = named_response(steps=[])
    completion = named_response(
        operation=operation, steps=named_response()["steps"] if operation == "act" else []
    )
    for step in completion["steps"]:
        step["intent"] = "fill"
    brain, model, _, _, _, _ = setup_brain(
        tmp_path, record, [initial, completion], task_instruction=None
    )
    brain._observe()
    result = brain._select_node()
    assert len(model.prompts) == 2
    if operation == "act":
        assert result == "review"
        assert brain._review_node() == "execution"
        assert brain.action_plan.actions[0].decision.action.source == "hbl_no"
    else:
        assert result == "finish"
        assert brain.result.reason == "model_requested_stop"


def test_missing_action_completion_cannot_repeat_empty_act():
    from pydantic import ValidationError

    from brain.graph.context_recall import ActionCompletion

    with pytest.raises(ValidationError, match="act_requires_steps"):
        ActionCompletion(operation="act", steps=[])


def test_final_proof_preserves_grid_row_and_field_evidence():
    from test_brain import element

    observed = wire()
    prefix = "e" + str(observed.obs_id.int) + "0"
    scene = SceneObservation(
        wire=observed,
        screen_kind="query",
        elements=[
            element(prefix + "1", "table"),
            element(prefix + "2", "row", parent=prefix + "1"),
            element(prefix + "3", "cell", parent=prefix + "2", value="test"),
        ],
    )
    result = ScreenMatch(
        screen_kind="query",
        controls=[],
        procedure_step="verify-final",
        operation="complete",
        grid=1,
        row=2,
        evidence=[{"field": "hbl_no", "target": 3}],
    )
    decision = compile_match(result, scene, DecisionResponse).to_decision()
    assert decision.grid_ref == prefix + "1" and decision.row_ref == prefix + "2"
    assert decision.evidence[0].ref == prefix + "3"


def test_selection_row_and_confirmation_compile_to_existing_executor():
    from types import SimpleNamespace
    from uuid import uuid4

    from brain.graph.context_recall import compile_screen_action_plan

    obs_id = uuid4()
    prefix = "e" + str(obs_id.int) + "0"
    scene = SimpleNamespace(
        obs_id=obs_id, elements=[SimpleNamespace(ref=prefix + str(i)) for i in (20, 31)]
    )
    match = ScreenMatch.model_validate(
        {
            "screen_kind": "partner",
            "controls": [],
            "procedure_step": "lookup-partner",
            "steps": [
                {
                    "target": 20,
                    "action": "click",
                    "source": "consignee",
                    "intent": "select_option",
                    "point": [1, 1],
                },
                {
                    "target": 31,
                    "action": "click",
                    "source": "consignee",
                    "intent": "select_option",
                    "point": [1, 1],
                },
            ],
        }
    )
    plan = compile_screen_action_plan(match, scene)
    assert [a.decision.action.ref for a in plan.actions] == [prefix + "20", prefix + "31"]
    assert all(a.decision.action.source is None for a in plan.actions)
    assert [a.effect_source for a in plan.actions] == [None, "consignee"]
    assert plan.verification == "single"
    mixed = match.model_copy(
        update={"steps": [match.steps[0], match.steps[1].model_copy(update={"intent": "navigate"})]}
    )
    assert [a.decision.intent for a in compile_screen_action_plan(mixed, scene).actions] == [
        "select_option",
        "navigate",
    ]


def test_selection_plan_preserves_optional_final_record_effect():
    from types import SimpleNamespace
    from uuid import uuid4

    from brain.graph.context_recall import compile_screen_action_plan

    obs_id = uuid4()
    prefix = "e" + str(obs_id.int) + "0"
    scene = SimpleNamespace(obs_id=obs_id, elements=[SimpleNamespace(ref=prefix + "31")])
    match = ScreenMatch.model_validate(
        {
            "screen_kind": "partner",
            "controls": [],
            "procedure_step": "lookup-partner",
            "steps": [
                {
                    "target": 31,
                    "action": "click",
                    "intent": "select_option",
                    "point": [1, 1],
                }
            ],
        }
    )
    assert compile_screen_action_plan(match, scene).actions[0].effect_source is None
    sourced = match.model_copy(
        update={"steps": [match.steps[0].model_copy(update={"source": "shipper"})]}
    )
    plan = compile_screen_action_plan(sourced, scene)
    assert plan.actions[0].effect_source == "shipper"


def test_unique_procedure_field_binds_selection_effect_without_model_echo():
    from brain.graph.context_recall import ActionCapability, bind_selection_effect

    match = ScreenMatch.model_validate(
        {
            "screen_kind": "partner",
            "controls": [],
            "procedure_step": "lookup-partner",
            "steps": [
                {
                    "target": 18,
                    "action": "click",
                    "intent": "select_option",
                    "point": [200, 170],
                },
                {
                    "target": 39,
                    "action": "click",
                    "intent": "select_option",
                    "point": [87, 539],
                },
            ],
        }
    )
    step = ActionCapability.model_validate(
        {
            "input_scope": ["shipper"],
            "goal": "Select the matching partner",
            "completion": "The editor field matches",
            "actions": ["select_option"],
        }
    )

    bound = bind_selection_effect(match, step, {"shipper"})
    assert [item.source for item in bound.steps] == [None, "shipper"]

    ambiguous = step.model_copy(update={"input_scope": ["shipper", "consignee"]})
    unchanged = bind_selection_effect(match, ambiguous, {"shipper", "consignee"})
    assert all(item.source is None for item in unchanged.steps)


def test_selected_record_value_is_verified_on_return_to_editor(tmp_path, record):
    from test_brain import element

    from brain.graph.schemas import Choice, Decision
    from brain.observe.progress import navigation_state, navigation_target
    from brain.observe.summary import describe

    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase, brain.duplicate_clear = "enter", True

    old_wire = wire()
    old_field = element("e101", "Edit", name="Shipper", value="")
    old_editor = SceneObservation(wire=old_wire, screen_kind="editor", elements=[old_field])
    partner_wire = wire()
    select = element("e201", "Button", name="Select")
    partner = SceneObservation(wire=partner_wire, screen_kind="partner", elements=[select])
    new_wire = wire()
    current_field = element("e301", "Edit", name="Shipper", value=brain.values["shipper"])
    editor = SceneObservation(wire=new_wire, screen_kind="editor", elements=[current_field])
    decision = Decision(
        screen_kind="partner",
        operation="act",
        intent="select_option",
        subgoal="lookup-partner",
        reason="Apply the selected shipper",
        action=Choice(type="click", ref=select.ref),
    )
    brain.observation = partner
    brain.deferred_field_checks["shipper"] = describe(old_field, old_editor.elements)
    brain.pending = {
        "before_observation": partner,
        "choice": decision.action,
        "decision": decision,
        "source_window_handle": partner.active_window.handle,
        "source_navigation_state": navigation_state(partner),
        "progress_scope": f"enter:fill_fields:{brain.business_revision}",
        "click_target": navigation_target(select, partner),
        "effect_source": "shipper",
    }

    def observe():
        brain.observation = editor
        return editor

    brain._observe = observe
    assert brain._verify_node() == "review"
    assert "shipper" in brain.verified
    assert "shipper" not in brain.deferred_field_checks
    assert any(
        event.payload.get("check") == "selected_field_value_matches"
        for event in brain.log.read_all()
    )


def test_save_matching_receives_same_action_contract_as_validator(tmp_path, record):
    from brain.graph.save_review import SAVE_RESULT_ACTIONS, SAVE_REVIEW_GUIDANCE

    brain, model, _, _, _, meaning = setup_brain(
        tmp_path,
        record,
        [
            named_response(
                screen_kind="dialog",
                procedure_step="inspect-save",
                steps=[],
                operation="stop",
                save_result="unknown",
            )
        ],
    )
    brain.phase = "save_result"
    brain._recall_visual_context(
        {
            "ocr": [b.model_dump() for b in fixture()[0]],
            "markers": [m.model_dump() for m in fixture()[2]],
        },
        None,
    )
    state = json.loads(model.prompts[-1][len(CONTEXT_MAPPING_RULES) :])
    expected = {
        "allowed_intents": {
            result: sorted(intents) for result, intents in SAVE_RESULT_ACTIONS.items()
        },
        "instructions": SAVE_REVIEW_GUIDANCE,
        "schema_mapping": {
            "result": "save_result",
            "evidence_refs": "message_targets",
            "next_decision": "steps",
        },
    }
    assert state["save_review"] == state["execution"]["save_review"] == expected


@pytest.mark.parametrize("phase", ["duplicate_query", "final_query"])
def test_pending_query_input_proposes_search_without_claiming_verification(tmp_path, record, phase):
    from brain.graph.schemas import Choice, Decision

    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = phase
    brain.decision = Decision(
        screen_kind="query",
        operation="act",
        intent="query_input",
        subgoal="query",
        reason="input identifier",
        action=Choice(type="set_text", ref="e1", source="hbl_no"),
    )
    brain.pending = {"decision": brain.decision}
    brain.settings = brain.settings.model_copy(update={"screen_matching": False})
    brain.task_instruction = None
    contract = brain._execution_context(anticipate=True)
    assert contract["checkpoint"] == "search" and contract["conditional"]
    assert "search" in contract["allowed_intents"]
    assert "query_input" not in contract["allowed_intents"]
    assert not brain.query_value_verified


def test_delivered_final_search_exposes_only_evidence_step(tmp_path, record):
    from brain.graph.schemas import Choice, Decision

    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "final_query"
    brain.duplicate_clear = brain.save_issued = True
    brain.query_value_verified = brain.query_submitted = True
    brain.decision = Decision(
        screen_kind="query",
        operation="act",
        intent="search",
        subgoal="search-final",
        reason="refresh result",
        action=Choice(type="click", ref="e1"),
    )
    brain.pending = {"decision": brain.decision}

    brain.settings = brain.settings.model_copy(update={"screen_matching": False})
    brain.task_instruction = None
    contract = brain._execution_context(anticipate=True)
    assert contract["checkpoint"] == "final_row_evidence"
    assert not {"query_input", "search"} & set(contract["allowed_intents"])


@pytest.mark.parametrize("pending", [False, True])
def test_task_prompt_separates_available_pending_and_verified_inputs(tmp_path, record, pending):
    from brain.graph.schemas import Choice, Decision

    procedure_step = "save-entry" if pending else "fill-additional-entry"
    brain, model, _, _, _, _ = setup_brain(
        tmp_path,
        record,
        [
            named_response(
                screen_kind="editor",
                procedure_step=procedure_step,
                steps=[],
                operation="stop",
            )
        ],
    )
    brain.phase = "task"
    brain.duplicate_clear = True
    brain.verified = {field: f"prior-{field}" for field in brain.values if field != "consignee"}
    if pending:
        brain.decision = Decision(
            screen_kind="editor",
            operation="act",
            intent="fill",
            subgoal="fill-entry",
            reason="fill final field",
            action=Choice(type="set_text", ref="e1", source="consignee"),
        )
        brain.pending = {"decision": brain.decision}

    brain._recall_visual_context(
        {
            "ocr": [block.model_dump() for block in fixture()[0]],
            "markers": [marker.model_dump() for marker in fixture()[2]],
        },
        None,
    )
    state = json.loads(model.prompts[-1][len(CONTEXT_MAPPING_RULES) :])
    notebook = state["notebook"]
    assert set(notebook["inputs"]) == set(brain.values)
    assert notebook["remaining_inputs"] == ["consignee"]
    assert notebook["pending_verification_inputs"] == (["consignee"] if pending else [])
    assert set(state["capability"]["input_scope"]) == set(brain.values)
    assert "consignee" not in brain.verified
    assert "procedure" not in state


def test_match_plan_rejects_an_action_outside_current_capability(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    capability = task_action_contract(
        brain.phase,
        brain.task_instruction,
        {"allowed_intents": ["navigate"]},
        set(brain.values),
    )
    proposal = ScreenMatch.model_validate(response(fixture()[-1], procedure_step="task-action"))
    request, services = matching_contract(brain)
    with pytest.raises(ValueError, match="action_outside_procedure"):
        validate_match_plan(proposal, capability, request, services, set(brain.values))


def test_observe_only_records_policy_scope_and_disabled_target_without_rejecting(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})
    capability = task_action_contract(
        brain.phase,
        brain.task_instruction,
        {"allowed_intents": ["navigate"]},
        {"shipper"},
    )
    proposal = ScreenMatch.model_validate(response(fixture()[-1], procedure_step="task-action"))
    proposal = proposal.model_copy(
        update={
            "controls": [item.model_copy(update={"enabled": False}) for item in proposal.controls]
        }
    )
    request, services = matching_contract(brain)

    accepted = validate_match_plan(proposal, capability, request, services, set(brain.values))

    assert accepted.steps[0].source == "hbl_no"
    observed = [
        event.payload
        for event in brain.log.read_all()
        if event.payload.get("check") == "guard_observe_only"
    ]
    assert {item["guard"] for item in observed} == {
        "action_boundary",
        "input_scope",
        "action_target",
    }


def test_pending_fields_remain_evidence_context_without_claiming_delivery(tmp_path, record):
    from brain.graph.schemas import Choice, Decision

    fixture()[-1]
    brain, model, _, _, _, _ = setup_brain(
        tmp_path,
        record,
        [
            named_response(
                screen_kind="editor",
                procedure_step="open-shipper-lookup",
                steps=[],
                operation="stop",
            )
        ],
    )
    brain.phase = "task"
    brain.duplicate_clear = True
    brain.verified = {
        field: f"prior-{field}" for field in brain.values if field not in {"hbl_no", "shipper"}
    }
    brain.decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="fill",
        subgoal="fill-entry",
        reason="fill pending field",
        action=Choice(type="set_text", ref="e1", source="hbl_no"),
    )
    brain.pending = {"decision": brain.decision}

    brain._recall_visual_context(
        {
            "ocr": [block.model_dump() for block in fixture()[0]],
            "markers": [marker.model_dump() for marker in fixture()[2]],
        },
        None,
    )

    state = json.loads(model.prompts[-1][len(CONTEXT_MAPPING_RULES) :])
    assert state["notebook"]["pending_verification_inputs"] == ["hbl_no"]
    assert set(state["notebook"]["remaining_inputs"]) == {"hbl_no", "shipper"}
    assert "hbl_no" not in brain.verified
    assert "hbl_no" in state["capability"]["input_scope"]
    assert "procedure" not in state


def test_input_outside_explicit_capability_scope_is_rejected(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    capability = task_action_contract(
        brain.phase,
        brain.task_instruction,
        {"allowed_intents": ["fill"]},
        {"shipper"},
    )
    proposal = ScreenMatch.model_validate(response(fixture()[-1], procedure_step="task-action"))
    request, services = matching_contract(brain)
    with pytest.raises(ValueError, match="field_outside_procedure_scope"):
        validate_match_plan(proposal, capability, request, services, set(brain.values))


def test_final_row_matching_receives_visibility_and_action_contract(tmp_path, record):

    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "final_query"
    brain.query_value_verified = brain.query_submitted = True
    brain.settings = brain.settings.model_copy(update={"screen_matching": False})
    brain.task_instruction = None
    contract = brain._execution_context(anticipate=True)
    assert "search" not in contract["allowed_intents"]
    assert "window expansion" in contract["guidance"]
    assert "reuse earlier refs" in contract["guidance"]


@pytest.mark.parametrize(
    "dependency", ["none", "action", "evidence", "parent", "verification", "other_verification"]
)
def test_only_unused_invalid_roles_are_omitted(tmp_path, record, dependency):
    from brain.graph.context_recall import sanitize_unused_controls
    from brain.visual.markers import Marker, ground_meanings
    from brain.visual.scene import SemanticDetection, bind_scene
    from pipeline.schemas import OcrBlock

    brain, *_ = setup_brain(tmp_path, record, [])
    blocks = [
        OcrBlock(id=4, text="Number", bbox=(10, 10, 80, 20), confidence=1),
        OcrBlock(id=5, text="Details", bbox=(10, 60, 80, 20), confidence=1),
    ]
    markers = [
        Marker(id=i, bbox=b.bbox, source="ocr", text_ids=(b.id,), confidence=1)
        for i, b in zip([5, 55], blocks, strict=True)
    ]
    payload = {
        "screen_kind": "query",
        "procedure_step": "verify-final",
        "controls": [
            {"id": 5, "role": "edit", "text_ids": [4], "confidence": 1},
            {"id": 55, "role": "button", "text_ids": [5], "confidence": 1},
        ],
        "steps": [{"target": 55, "action": "click", "intent": "navigate", "point": [50, 70]}],
    }
    required = []
    if dependency == "action":
        payload["steps"][0]["target"] = 5
    elif dependency == "evidence":
        payload["evidence"] = [{"field": "hbl_no", "target": 5}]
    elif dependency == "parent":
        payload["controls"][1]["parent_id"] = 5
    elif dependency == "verification":
        required = [{"screen": "query", "descriptor": {"role": "edit", "name": ""}}]
    elif dependency == "other_verification":
        required = [{"screen": "query", "descriptor": {"role": "edit", "name": "Other"}}]
    response = ScreenMatch.model_validate(payload)
    cleaned = sanitize_unused_controls(
        matching_contract(brain)[1],
        brain.phase,
        response,
        {"markers": [m.model_dump() for m in markers], "ocr": [b.model_dump() for b in blocks]},
        required,
    )
    meanings = SemanticDetection.model_validate(
        {k: v for k, v in cleaned.model_dump().items() if k in SemanticDetection.model_fields}
    )
    if dependency in {"none", "parent", "other_verification"}:
        scene = bind_scene(wire(), blocks, ground_meanings(meanings, markers, blocks))
        decision = compile_match(cleaned, scene, DecisionResponse).decision
        assert decision.intent == "navigate"
        assert next(e for e in scene.elements if e.ref == decision.action.ref).name == "Details"
        assert not any(e.role == "edit" for e in scene.elements)
        if dependency == "parent":
            assert (
                next(e for e in scene.elements if e.ref == decision.action.ref).parent_ref is None
            )
    else:
        with pytest.raises(ValueError, match="ocr_text_is_not_control_extent"):
            ground_meanings(meanings, markers, blocks)


@pytest.mark.parametrize("dependency", ["unrelated", "dangling_unused", "action", "direct_proof"])
def test_only_nonproof_invalid_parent_links_are_omitted(tmp_path, record, dependency):
    from brain.graph.context_recall import sanitize_unused_controls
    from brain.visual.markers import Marker, ground_meanings
    from brain.visual.scene import SemanticDetection, bind_scene
    from pipeline.schemas import OcrBlock

    brain, *_ = setup_brain(tmp_path, record, [])
    blocks = [OcrBlock(id=0, text="조회 결과 0건", bbox=(10, 110, 70, 15), confidence=1)]
    markers = [
        Marker(id=16, bbox=(0, 0, 100, 100), source="omniparser", confidence=1),
        Marker(id=31, bbox=blocks[0].bbox, source="ocr", text_ids=(0,), confidence=1),
        Marker(id=34, bbox=(120, 110, 80, 25), source="omniparser", confidence=1),
    ]
    payload = {
        "screen_kind": "query",
        "procedure_step": "open-entry",
        "controls": [
            {"id": 16, "role": "table", "confidence": 1, "table_state": "empty"},
            {
                "id": 31,
                "role": "text",
                "parent_id": 16,
                "text_ids": [0],
                "confidence": 1,
            },
            {"id": 34, "role": "button", "confidence": 1},
        ],
        "steps": [
            {
                "target": 31 if dependency == "action" else 34,
                "action": "click",
                "intent": "navigate",
                "point": [150, 120],
            }
        ],
        # This field is irrelevant without save_result and must not promote decoration to proof.
        "message_targets": [31],
    }
    if dependency == "direct_proof":
        payload["grid"] = 16
        payload["evidence"] = [{"field": "hbl_no", "target": 31}]
    elif dependency == "dangling_unused":
        # A semantic response may identify a nested but unused decoration while
        # omitting its parent control. That relationship must not invalidate the
        # unrelated action plan during scene binding.
        payload["controls"][1]["parent_id"] = 16
        payload["controls"] = [payload["controls"][1], payload["controls"][2]]
    response = sanitize_unused_controls(
        matching_contract(brain)[1],
        brain.phase,
        ScreenMatch.model_validate(payload),
        {
            "markers": [marker.model_dump() for marker in markers],
            "ocr": [block.model_dump() for block in blocks],
        },
        [],
    )
    meanings = SemanticDetection.model_validate(
        {
            key: value
            for key, value in response.model_dump().items()
            if key in SemanticDetection.model_fields
        }
    )
    scene = bind_scene(wire(), blocks, ground_meanings(meanings, markers, blocks))
    result_count = next(element for element in scene.elements if element.value == "조회 결과 0건")
    assert result_count.parent_ref is None


@pytest.mark.parametrize("verified", [False, True])
def test_control_cleanup_preserves_current_evidence_regardless_of_prior_verification(
    tmp_path, record, verified
):
    from brain.graph.context_recall import sanitize_unused_controls
    from brain.visual.markers import Marker, ground_meanings
    from brain.visual.scene import SemanticDetection, bind_scene

    brain, *_ = setup_brain(tmp_path, record, [])
    if verified:
        brain.verified["hbl_no"] = "prior-editor-ref"
    markers = [
        Marker(id=31, bbox=(10, 10, 80, 25), source="omniparser", confidence=1),
        Marker(id=34, bbox=(120, 10, 80, 25), source="omniparser", confidence=1),
    ]
    response = sanitize_unused_controls(
        matching_contract(brain)[1],
        brain.phase,
        ScreenMatch.model_validate(
            {
                "screen_kind": "editor",
                "procedure_step": "save-entry",
                "controls": [{"id": 34, "role": "button", "confidence": 1}],
                "steps": [{"target": 34, "action": "click", "intent": "save", "point": [150, 20]}],
                "evidence": [{"field": "hbl_no", "target": 31}],
                "save_result": "unknown",
            }
        ),
        {"markers": [marker.model_dump() for marker in markers], "ocr": []},
        [],
    )
    assert response.save_result is None
    assert response.evidence[0].target == 31
    meanings = SemanticDetection.model_validate(
        {
            key: value
            for key, value in response.model_dump().items()
            if key in SemanticDetection.model_fields
        }
    )
    scene = bind_scene(wire(), [], ground_meanings(meanings, markers, []))
    with pytest.raises(ValueError, match="visual_proposal_unknown_marker"):
        compile_match(response, scene, DecisionResponse)
    checks = {event.payload.get("check") for event in brain.log.read_all()}
    assert "unbound_action_evidence_omitted" not in checks
    assert "stale_verified_evidence_omitted" not in checks
    assert ("hbl_no" in brain.verified) is verified


def test_education_retrieval_replans_before_any_provisional_action(tmp_path, record):
    taught = TaskInstruction.model_validate_json(
        (
            Path(__file__).resolve().parents[1] / "examples/shipment-user-taught-task.json"
        ).read_bytes()
    )
    request_details = named_response(requested_instruction_ids=["save-record"])
    resolved = named_response(operation="stop", steps=[])
    brain, model, *_ = setup_brain(
        tmp_path, record, [request_details, resolved], task_instruction=taught
    )
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})
    brain._observe()
    assert len(model.prompts) == 2
    assert '"retrieved_step_ids": ["save-record"]' in model.prompts[1]
    expected = next(step for step in taught.procedure if step.id == "save-record")
    assert expected.completion in model.prompts[1]
    expanded = json.loads(model.prompts[1][len(CONTEXT_MAPPING_RULES) :])
    assert {step["id"] for step in expanded["taught_procedure_progress"]["steps"]} >= {
        "login",
        "save-record",
    }
    assert brain.visual_proposal.steps == []
    assert brain.completed_taught_steps == set()
    events = brain.log.read_all()
    assert not any(event.kind == "act" for event in events)
    retrieval = next(e for e in events if e.payload.get("check") == "education_details_retrieved")
    assert retrieval.payload["discarded_provisional_actions"] == 1
    calls = [e for e in events if e.kind == "model_call"]
    assert [e.payload["context"]["purpose"] for e in calls] == [
        "screen_decision",
        "education_retrieval",
    ]
    assert calls[0].payload["context"]["decision_id"] == calls[1].payload["context"]["decision_id"]


def test_synthetic_education_expansion_survives_empty_action_recovery(tmp_path, record):
    # Synthetic D-1 replay using the existing fake, not a recorded pilot response.
    taught = TaskInstruction.model_validate_json(
        (
            Path(__file__).resolve().parents[1] / "examples/shipment-user-taught-task.json"
        ).read_bytes()
    )
    brain, model, *_ = setup_brain(
        tmp_path,
        record,
        [
            named_response(requested_instruction_ids=["save-record"]),
            named_response(operation="act", steps=[]),
            named_response(operation="stop", steps=[]),
        ],
        task_instruction=taught,
    )
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})

    brain._observe()

    purposes = [
        event.payload["context"]["purpose"]
        for event in brain.log.read_all()
        if event.kind == "model_call"
    ]
    assert purposes == ["screen_decision", "education_retrieval", "missing_actions_recovery"]
    initial, expanded, recovered = [
        json.loads(prompt[len(CONTEXT_MAPPING_RULES) :]) for prompt in model.prompts
    ]
    assert [step["id"] for step in initial["taught_procedure_progress"]["steps"]] == [
        "login",
        "open-query",
    ]
    expected = next(step for step in taught.procedure if step.id == "save-record")
    assert expanded["taught_procedure_progress"]["steps"][-1] == expected.model_dump(mode="json")
    assert recovered["taught_procedure_progress"] == expanded["taught_procedure_progress"]
    assert brain.visual_proposal.operation == "stop"
    assert brain.completed_taught_steps == set()
    assert not any(event.kind == "act" for event in brain.log.read_all())


def test_synthetic_fallback_education_survives_empty_action_recovery(tmp_path, record):
    # Synthetic input using the existing fake: supply an invalid cached proposal
    # at the method boundary, but run selection, validation and recovery unchanged.
    taught = TaskInstruction.model_validate_json(
        (
            Path(__file__).resolve().parents[1] / "examples/shipment-user-taught-task.json"
        ).read_bytes()
    )
    brain, model, *_ = setup_brain(
        tmp_path,
        record,
        [
            named_response(requested_instruction_ids=["save-record"]),
            named_response(operation="act", steps=[]),
            named_response(operation="stop", steps=[]),
        ],
        task_instruction=taught,
    )
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})
    blocks, _, markers, _ = fixture()
    label = next(marker for marker in markers if marker.source == "ocr")
    screen = {
        "image_size": [400, 200],
        "ocr": [block.model_dump() for block in blocks],
        "markers": [marker.model_dump() for marker in markers],
    }
    proposal = ScreenMatch.model_validate(
        {
            "screen_kind": "query",
            "procedure_step": "task-action",
            "controls": [{"id": label.id, "role": "edit", "confidence": 1}],
            "steps": [
                {
                    "target": label.id,
                    "action": "fill",
                    "intent": "query_input",
                    "source": "hbl_no",
                    "point": [40, 27],
                }
            ],
        }
    )
    request, services = matching_contract(brain, screen)

    result = recall_context(replace(request, proposal=proposal), services)

    events = brain.log.read_all()
    fallback = [event for event in events if event.payload.get("check") == "method_fallback"]
    assert len(fallback) == 1
    assert fallback[0].payload["reason"] == "screen_match_action_surface_mismatch"
    calls = [event for event in events if event.kind == "model_call"]
    assert fallback[0].seq < calls[0].seq
    assert [event.payload["context"]["purpose"] for event in calls] == [
        "screen_decision",
        "education_retrieval",
        "missing_actions_recovery",
    ]
    initial, expanded, recovered = [
        json.loads(prompt[len(CONTEXT_MAPPING_RULES) :]) for prompt in model.prompts
    ]
    assert "save-record" not in {
        step["id"] for step in initial["taught_procedure_progress"]["steps"]
    }
    expected = next(step for step in taught.procedure if step.id == "save-record")
    assert expanded["taught_procedure_progress"]["steps"][-1] == expected.model_dump(mode="json")
    assert recovered["taught_procedure_progress"] == expanded["taught_procedure_progress"]
    assert result.response.operation == "stop"
    assert result.response.steps == []
    assert not any(event.kind == "act" for event in events)


def test_supplied_education_preserves_actions_without_another_model_call(tmp_path, record):
    # Synthetic reduction of 066 calls 17/21: requested details already supplied.
    taught = TaskInstruction.model_validate_json(
        (
            Path(__file__).resolve().parents[1] / "examples/shipment-user-taught-task.json"
        ).read_bytes()
    )
    request_details = named_response(requested_instruction_ids=["login"])
    brain, model, *_ = setup_brain(tmp_path, record, [request_details], task_instruction=taught)
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})

    brain._observe()

    assert len(model.prompts) == 1
    sent = json.loads(model.prompts[0][len(CONTEXT_MAPPING_RULES) :])
    supplied = {step["id"]: step for step in sent["taught_procedure_progress"]["steps"]}
    assert supplied["login"] == next(
        step for step in taught.procedure if step.id == "login"
    ).model_dump(mode="json")
    assert brain.visual_proposal.steps
    assert brain.visual_proposal.requested_instruction_ids == []
    check = next(
        event
        for event in brain.log.read_all()
        if event.payload.get("check") == "resolved_education_request_ignored"
    )
    assert check.payload == {
        "check": "resolved_education_request_ignored",
        "step_ids": ["login"],
        "accepted_actions": 1,
    }


@pytest.mark.parametrize("guard_mode", ["enforce", "observe_only"])
def test_compound_claim_survives_before_action_without_becoming_verified(
    tmp_path, record, guard_mode
):
    data = named_response(operation="duplicate_clear")
    brain, model, *_ = setup_brain(tmp_path, record, [data])
    brain.settings = brain.settings.model_copy(update={"guard_mode": guard_mode})
    brain._observe()
    assert brain.visual_proposal.operation == "act"
    assert brain.visual_proposal.steps
    assert brain.duplicate_clear is False
    claim = next(item for item in brain.feedback if item.get("check") == "model_observation_claim")
    assert claim["operation"] == "duplicate_clear"
    assert claim["status"] == "reported_not_independently_verified"
    assert len(model.prompts) == 1
    assert not any(event.kind == "act" for event in brain.log.read_all())


@pytest.mark.parametrize(
    "metadata", ["supporting-role", "confidence", "description", "long-description", "field-order"]
)
def test_action_owned_control_normalizes_once_and_keeps_cache_contract(metadata):
    # Synthetic counterexamples from the 044--047 report; actual 047 response was not retained.
    import copy

    control = {"id": 8, "role": "edit", "confidence": 1, "label_ids": [3]}
    raw = {
        "screen_kind": "editor",
        "procedure_step": "task-action",
        "controls": [],
        "steps": [
            {
                "target": control,
                "action": "fill",
                "intent": "fill",
                "source": "hbl_no",
                "point": [190.25, 50.75],
            },
        ],
    }
    original = copy.deepcopy(raw)
    result = ScreenMatch.model_validate(raw)
    assert raw == original
    assert result.controls[0].id == result.steps[0].target == 8
    assert result.controls[0].label_ids == [3]
    assert result.steps[0].point == [190.25, 50.75]
    assert ScreenMatch.model_validate_json(result.model_dump_json()) == result
    schema = ScreenMatch.model_json_schema()
    assert schema["$defs"]["NamedAction"]["properties"]["target"]["type"] == "string"
    assert "controls" not in schema["properties"]
    assert "maxItems" not in json.dumps(schema)
    stored_schema = ScreenMatch.model_json_schema(mode="serialization")
    assert stored_schema["$defs"]["MatchAction"]["properties"]["target"]["type"] == "integer"
    raw["controls"] = [dict(control)]
    if metadata == "supporting-role":
        raw["controls"][0].update(
            role="button", confidence=0.1, visual_description="supporting observation"
        )
    elif metadata == "confidence":
        raw["controls"][0]["confidence"] = 0.95
    elif metadata == "description":
        raw["controls"][0]["visual_description"] = "The same input described differently"
    elif metadata == "long-description":
        raw["controls"][0]["visual_description"] = "Supporting observation. " * 20
    else:
        raw = dict(reversed(list(raw.items())))
        raw["controls"][0] = dict(reversed(list(raw["controls"][0].items())))
    normalized = ScreenMatch.model_validate(raw)
    assert normalized.steps == result.steps
    assert normalized.controls[0].role == "edit"
    assert normalized.controls[0].confidence == 1
    assert normalized.controls[0].visual_description == ""


@pytest.mark.parametrize("model_created", [False, True], ids=["detected", "model-created"])
def test_inline_target_reaches_wire_with_exact_fill_point_without_recovery(
    tmp_path, record, model_created
):
    data = named_response()
    data["steps"][0]["point"] = [175.25, 30.75]
    brain, model, *_ = setup_brain(tmp_path, record, [data])
    if model_created:
        brain.screen_scanner.detector.read = lambda *args, **kwargs: []
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})
    brain._observe()
    assert brain._select_node() == "review"
    selected = brain.action_plan.actions[0].decision
    wired = brain._wire_action(selected.action, selected)
    assert wired.root.target.image_point == (175.25, 30.75)
    assert wired.root.target.evidence.startswith("image_planned:")
    assert selected.action.source == "hbl_no"
    assert len(model.prompts) == 1  # No target-semantics supplement or coordinate-repair call.


def test_semantic_model_mapping_becomes_observation_and_coordinate_method(tmp_path, record):
    from brain.cache.methods import MethodLibrary, method_from_match
    from brain.graph.screen_mapping import ScreenMapping
    from brain.visual.action_rebind import perceptual_hash

    raw = {
        "screen_kind": "query",
        "fields": {"Reference": {"role": "edit", "bbox": [100, 15, 150, 30], "value": ""}},
        "assignments": {"Reference": "hbl_no"},
        "steps": [{"target": "Reference", "action": "fill", "intent": "fill", "point": [125, 28]}],
    }
    brain, model, _, _, _, _ = setup_brain(tmp_path, record, [raw])
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})
    brain._observe()
    target = next(e for e in brain.observation.elements if e.role == "edit")
    assert target.name == "Reference" and target.bbox == (100, 15, 150, 30)
    assert brain._select_node() == "review"
    assert brain.action_plan.actions[0].decision.action.source == "hbl_no"
    # Raw provider artifact is semantic, executor refs are generated afterwards.
    artifact = next((tmp_path / "matching").glob("*-semantic-mapping.json"))
    assert json.loads(artifact.read_text(encoding="utf-8"))["assignments"] == {
        "Reference": "hbl_no"
    }
    # Check the actual provider prompt after ScreenMapping rebuilds it.
    assert model.prompts[0].startswith("Perform Context Recall")
    for stage in ("Scanning", "Pattern Matching", "Memory Retrieval"):
        assert stage in model.prompts[0], stage
    assert '"id_namespace"' not in model.prompts[0]
    assert '"text_regions"' in model.prompts[0]
    schema = ScreenMatch.model_json_schema()
    assert schema["$defs"]["NamedAction"]["properties"]["target"]["type"] == "string"
    screen = brain.screen_scanner.planning_context()[0].screen
    mapping = ScreenMapping.model_validate(raw)
    payload = mapping.local_payload(screen)
    learned = method_from_match(screen, payload, method_id="semantic", purpose="Query")
    assert learned is not None
    chosen = learned.action_groups[0].targets[0]
    assert chosen.name == "Reference" and chosen.point == (125, 28)
    library = MethodLibrary(tmp_path / "coordinate-methods.json", enabled=True)
    library.upsert(learned)
    # No OCR text, no detector numbers, and a different input key/value on replay.
    fresh = {
        "image_size": [400, 200],
        "screen_phash": screen["screen_phash"],
        "ocr": [],
        "markers": [],
    }
    bound = library.bind("semantic", {"hbl_no": "next_number"}, fresh, {"next_number": "NEW-2"})
    assert bound is not None
    assert bound["steps"][0]["point"] == [125, 28]
    assert bound["steps"][0]["source"] == "next_number"
    assert len(model.prompts) == 1
    # A different frame or viewport must yield back to model selection.
    for change in (
        {"screen_phash": screen["screen_phash"] ^ ((1 << 64) - 1)},
        {"image_size": [800, 400]},
    ):
        assert library.bind("semantic", {}, {**fresh, **change}, {"hbl_no": "NEXT"}) is None
    assert screen["screen_phash"] == perceptual_hash(
        next((tmp_path / "visual").glob("*/capture.png"))
    )


def test_semantic_mapping_accepts_input_key_references_and_normalizes_evidence_field():
    from brain.graph.screen_mapping import ScreenMapping

    mapping = ScreenMapping.model_validate(
        {
            "screen_kind": "query",
            "fields": {
                "House B/L 번호": {
                    "role": "edit",
                    "bbox": [100, 15, 150, 30],
                    "value": "HBL-23",
                }
            },
            "assignments": {"House B/L 번호": "hbl_no"},
            "steps": [
                {
                    "target": "hbl_no",
                    "action": "fill",
                    "intent": "fill",
                    "point": [125, 28],
                }
            ],
            "evidence": [{"field": "House B/L 번호 입력값", "target": "hbl_no"}],
            "completion_evidence": [{"condition_index": 0, "target": "hbl_no"}],
        }
    )

    response = ScreenMatch.model_validate(mapping.local_payload({}))

    assert response.steps[0].source == "hbl_no"
    assert response.evidence[0].field == "hbl_no"
    assert response.evidence[0].target == response.steps[0].target
    assert response.completion_evidence[0].target == response.steps[0].target


@pytest.mark.parametrize("operation", ["act", "complete"])
def test_review_evidence_without_assignments_uses_input_keys(operation):
    from brain.graph.screen_mapping import ScreenMapping

    inputs = {
        "shipper": "Sender",
        "consignee": "Receiver",
        "mbl_no": "MBL-1",
        "left": "shared",
        "right": "shared",
    }
    visible = {
        "송하인": "Sender",
        "Consignee": "Receiver",
        "MBL No.": "MBL-1",
        "Unknown": "not supplied",
        "Ambiguous": "shared",
    }
    mapping = ScreenMapping(
        fields={
            name: {"role": "edit", "bbox": [0, 0, 100, 20], "value": value}
            for name, value in visible.items()
        },
        evidence=[{"field": name, "target": name} for name in visible],
        operation=operation,
    )
    screen = {}
    response = ScreenMatch.model_validate(mapping.local_payload(screen, inputs))
    assert [item.field for item in response.evidence] == ["shipper", "consignee", "mbl_no"]
    assert response.operation == operation
    assert {item["field"] for item in screen["unresolved_annotations"]} == {"Unknown", "Ambiguous"}
    ExecutionFacts(
        phase="task",
        field_names=tuple(inputs),
        verified_field_names=tuple(item.field for item in response.evidence),
    )
    mapping.assignments = {"Ambiguous": "right"}
    assert (
        ScreenMatch.model_validate(mapping.local_payload({}, inputs)).evidence[-1].field == "right"
    )


def test_semantic_mapping_resolves_unique_visible_value_and_ignores_irrelevant_repairs():
    from brain.graph.screen_mapping import ScreenMapping

    mapping = ScreenMapping.model_validate(
        {
            "screen_kind": "editor",
            "fields": {
                "추가 정보 수량": {
                    "role": "edit",
                    "bbox": [120, 193, 451, 32],
                    "value": "12",
                }
            },
            "assignments": {"추가 정보 수량": "packages.count"},
            "evidence": [{"field": "수량", "target": "12"}],
            "repair_fields": ["additional destination value"],
        }
    )

    response = ScreenMatch.model_validate(mapping.local_payload({}))

    assert response.evidence[0].field == "packages.count"
    assert response.repair_fields == []


def test_semantic_mapping_omits_unassigned_descriptive_evidence():
    from brain.graph.screen_mapping import ScreenMapping

    mapping = ScreenMapping.model_validate(
        {
            "screen_kind": "query",
            "fields": {
                "조회 결과": {"role": "table", "bbox": [30, 160, 1100, 500]},
            },
            "evidence": [
                {"field": "조회 결과가 비어 있음", "target": "조회 결과"},
                {"field": "설명뿐인 항목", "target": "존재하지 않는 대상"},
            ],
        }
    )

    response = ScreenMatch.model_validate(mapping.local_payload({}))

    assert response.evidence == []


def test_semantic_mapping_resolves_unique_result_roles_for_completion():
    from brain.graph.screen_mapping import ScreenMapping

    mapping = ScreenMapping.model_validate(
        {
            "screen_kind": "query",
            "fields": {
                "조회 결과": {"role": "table", "bbox": [30, 160, 1100, 500]},
                "저장된 결과 행": {"role": "row", "bbox": [30, 170, 1100, 45]},
            },
            "operation": "complete",
            "grid": "결과 그리드",
            "row": "1",
            "completion_evidence": [
                {"condition_index": 0, "target": "1"},
                {"condition_index": 1, "target": "1"},
            ],
        }
    )

    response = ScreenMatch.model_validate(mapping.local_payload({}))

    assert response.grid is not None
    assert response.row is not None
    assert {item.target for item in response.completion_evidence} == {response.row}


def test_semantic_mapping_resolves_unique_descriptive_name_fragment():
    from brain.graph.screen_mapping import ScreenMapping

    mapping = ScreenMapping.model_validate(
        {
            "screen_kind": "query",
            "fields": {
                "House B/L 조회 결과 목록": {
                    "role": "table",
                    "bbox": [30, 160, 1100, 500],
                },
                "Save Completion Close Button": {
                    "role": "button",
                    "bbox": [250, 240, 80, 45],
                },
            },
            "steps": [
                {
                    "target": "Save Completion Close",
                    "action": "click",
                    "intent": "navigate",
                    "point": [290, 262],
                }
            ],
            "grid": "조회 결과 목록",
        }
    )

    response = ScreenMatch.model_validate(mapping.local_payload({}))

    assert response.steps[0].target != response.grid
    assert response.grid is None  # An act plan does not consume result-grid metadata.


@pytest.mark.parametrize("separate", [False, True], ids=["combined", "reused-observation"])
def test_named_fields_replace_number_recovery_for_mixed_targets(tmp_path, record, separate):
    first = named_response()
    data = named_response(
        fields={
            "Reference": {"role": "edit", "bbox": [100, 15, 150, 30]},
            "New reference": {"role": "edit", "bbox": [100, 90, 150, 30]},
        },
        assignments={"Reference": "hbl_no", "New reference": "mbl_no"},
        steps=[
            {"target": "Reference", "action": "fill", "intent": "fill", "point": [125, 28]},
            {"target": "New reference", "action": "fill", "intent": "fill", "point": [126, 103]},
        ],
    )
    replies = [first, data] if separate else [data]
    brain, model, *_ = setup_brain(tmp_path, record, replies)
    brain._observe()
    if separate:
        brain._observe()
    assert brain._select_node() == "review"
    assert [a.decision.action.source for a in brain.action_plan.actions] == ["hbl_no", "mbl_no"]
    assert [
        brain.visual_points.get(brain.observation, a.decision).point
        for a in brain.action_plan.actions
    ] == [(125, 28), (126, 103)]
    assert len(model.prompts) == len(replies)


def test_named_missing_target_fails_without_number_grounding_retry(tmp_path, record):
    data = named_response()
    data["steps"][0]["target"] = "Missing field"
    brain, model, *_ = setup_brain(tmp_path, record, [data])
    with pytest.raises(KeyError, match="Missing field"):
        brain._observe()
    assert len(model.prompts) == 1
    assert not any(e.kind == "act" for e in brain.log.read_all())


@pytest.mark.parametrize("terminal", ["search", "save", "navigate"])
def test_named_input_then_command_executes_and_observes_once(tmp_path, record, terminal):
    from sawon_interfaces import ActionResult

    data = named_response(
        fields={
            "Reference": {"role": "edit", "bbox": [100, 15, 150, 30]},
            "Command": {"role": "button", "bbox": [270, 15, 90, 30]},
        },
        steps=[
            {"target": "Reference", "action": "fill", "intent": "fill", "point": [125, 28]},
            {"target": "Command", "action": "click", "intent": terminal, "point": [310, 28]},
        ],
    )
    after = named_response(operation="stop", steps=[], evidence=[])
    brain, model, relay, *_ = setup_brain(tmp_path, record, [data, after])
    brain.settings = brain.settings.model_copy(
        update={"guard_mode": "observe_only", "visual_model_only_review": True}
    )
    brain._observe()
    assert brain._select_node() == "review"
    assert [a.decision.intent for a in brain.action_plan.actions] == ["fill", terminal]
    assert brain._review_node() == "execution"
    sent = []

    def act(action):
        sent.append(action.root)
        if action.root.type == "click":
            relay.color = "black"
        return ActionResult(
            action_id=action.root.action_id,
            ok=True,
            duration_ms=0,
            post_obs_id=relay.current.obs_id,
        )

    relay.act = act
    assert brain._act_node() == "observation"
    assert [a.type for a in sent] == ["set_text", "click"]
    assert sent[0].text == brain.values["hbl_no"]
    assert [a.target.image_point for a in sent] == [(125.0, 28.0), (310.0, 28.0)]
    assert len(model.prompts) == 1
    assert "hbl_no" not in brain.verified
    assert len(brain.pending["execution"].delivered) == 2
    assert brain._verify_node() == "review"
    assert len(model.prompts) == 2
    assert brain.pending is None
    assert "hbl_no" not in brain.verified


@pytest.mark.parametrize("failure_index", [0, 1], ids=["input-fails", "command-fails"])
def test_named_mixed_plan_failure_records_only_delivered_prefix(tmp_path, record, failure_index):
    from sawon_interfaces import ActionResult

    data = named_response(
        fields={
            "Reference": {"role": "edit", "bbox": [100, 15, 150, 30]},
            "Command": {"role": "button", "bbox": [270, 15, 90, 30]},
        },
        steps=[
            {"target": "Reference", "action": "fill", "intent": "fill", "point": [125, 28]},
            {"target": "Command", "action": "click", "intent": "search", "point": [310, 28]},
        ],
    )
    brain, model, relay, *_ = setup_brain(tmp_path, record, [data])
    brain.settings = brain.settings.model_copy(update={"guard_mode": "observe_only"})
    brain._observe()
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    sent = []

    def act(action):
        sent.append(action.root)
        ok = len(sent) - 1 != failure_index
        return ActionResult(
            action_id=action.root.action_id,
            ok=ok,
            duration_ms=0,
            error=None if ok else "action_failed",
            post_obs_id=relay.current.obs_id,
        )

    relay.act = act
    brain._act_node()
    assert len(sent) == failure_index + 1
    assert len(model.prompts) == 1
    assert brain.pending is None
    assert "hbl_no" not in brain.verified
    assert any(e.payload.get("reason") == "relay_action_failed" for e in brain.log.read_all())


def test_mapping_preserves_review_and_retrieval_purpose_and_ignores_unused_grid():
    from brain.graph.screen_mapping import ScreenMapping, mapping_prompt

    state = {
        "screen": {},
        "post_action_review": {"delivered_sources": ["hbl_no"]},
        "taught_procedure_progress": {"retrieved_step_ids": ["details"]},
    }
    prompt = mapping_prompt("old numeric instructions" + json.dumps(state), {})
    payload = json.loads(prompt[len(CONTEXT_MAPPING_RULES) :])
    assert "already delivered" in payload["review_instruction"]
    assert "not another retrieval" in payload["education_instruction"]
    mapping = ScreenMapping.model_validate(named_response(grid="nonexistent grid", row="absent"))
    assert mapping.local_payload({})["grid"] is None
    assert len(mapping.local_payload({})["steps"]) == 1


def test_delivered_value_updates_batch_and_action_memory_without_duplicate_evidence(
    tmp_path, record
):
    from brain.graph.execution import ActionPlan, PlanExecution
    from brain.graph.schemas import Choice, Decision

    task_response = named_response(screen_kind="editor", evidence=[])
    task_response["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    brain, *_ = setup_brain(tmp_path, record, [task_response])
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="fill",
        subgoal="fill",
        reason="input",
        action=Choice(type="set_text", ref="old-frame", source="hbl_no"),
    )
    execution = PlanExecution(
        ActionPlan.single(decision),
        wire(),
        delivered=[{"choice": decision.action, "decision": decision}],
    )
    brain.pending = {"execution": execution}
    brain._observe()
    assert "hbl_no" in brain.verified
    assert brain._verify_screen_batch(execution, brain.observation) == "review"
    assert not any(
        e.payload.get("reason") == "screen_fill_evidence_missing" for e in brain.log.read_all()
    )
    assert brain.evidence_ledger.prompt_context()[0]["field"] == "hbl_no"
    assert brain.rolling_context.recent_turns[-1].target_summary == "set_text:hbl_no"
    target = next(e for e in brain.observation.elements if e.value == brain.values["hbl_no"])
    assert brain._record_verified_field("hbl_no", target)
    assert len(brain.evidence_ledger.history) == 1


@pytest.mark.parametrize("rejected_next_plan", [False, True], ids=["valid-plan", "rejected-plan"])
def test_detail_observation_commits_review_before_current_evidence(
    tmp_path, record, monkeypatch, rejected_next_plan
):
    from brain.graph.schemas import Choice, Decision

    task_response = named_response(
        screen_kind="editor",
        steps=[],
        operation="stop",
        evidence=[{"field": "hbl_no", "target": "Reference"}],
    )
    task_response["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    brain, model, *_ = setup_brain(tmp_path, record, [task_response])
    brain.settings = brain.settings.model_copy(update={"visual_model_only_review": True})
    brain.save_issued = True
    brain.verified = {field: f"entry-{field}" for field in brain.values}
    decision = Decision(
        screen_kind="query",
        operation="act",
        intent="navigate",
        subgoal="open",
        reason="review",
        action=Choice(type="double_click", ref="old-frame"),
    )
    brain.saved_detail_transition_pending = True
    brain.pending = {
        "decision": decision,
        "choice": decision.action,
    }
    if rejected_next_plan:

        def reject(*args, **kwargs):
            raise ValueError("screen_match_action_surface_mismatch")

        monkeypatch.setattr("brain.graph.context_recall.validate_or_replan", reject)
    brain._observe()
    assert brain.model_saved_detail_review
    assert "hbl_no" in brain.post_save_verified
    payload = json.loads(model.prompts[0][len(CONTEXT_MAPPING_RULES) :])
    assert payload["notebook"]["workflow_state"]["saved_result_open_pending"]
    assert payload["notebook"]["evidence_scope"] == "saved_detail_candidate"
    assert payload["notebook"]["verified_inputs"] == []
    assert payload["notebook"]["scratchpad"]["verified_inputs"] == []
    assert set(payload["execution"]["remaining_inputs"]) == set(brain.values)
    assert set(brain.verified) == set(brain.values)  # Entry history is not erased.
    checks = [event.payload.get("check") for event in brain.log.read_all()]
    assert checks.index("model_saved_detail_review_started") < checks.index(
        "model_task_evidence_recorded"
    )
    brain._begin_saved_detail_review("editor")
    assert "hbl_no" in brain.post_save_verified  # Later verification cannot clear it again.
    if rejected_next_plan:
        assert brain.visual_match_candidate is None
        brain.pending = None
        with pytest.raises(ValueError, match="screen_match_action_surface_mismatch"):
            brain._selection_call("", ScreenMatch)
        assert "hbl_no" in brain.post_save_verified
        assert "hbl_no" in brain.verified
        assert brain.model_saved_detail_review
        assert len(model.prompts) == 1


def test_different_exception_stages_do_not_share_a_subgoal_failure(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    assert brain._fail("verification_error:KeyError") == "observation"
    assert brain._fail("action_error:ValueError") == "observation"
    assert len(brain.failures) == 2


def test_unresolved_annotations_preserve_actions_without_inventing_proof():
    from brain.graph.screen_mapping import ScreenMapping

    data = named_response(
        message_targets=["Reference", "undeclared save summary"],
        completion_evidence=[
            {"condition_index": 0, "target": "Reference"},
            {"condition_index": 1, "target": "undeclared completion summary"},
        ],
    )
    screen = {}
    payload = ScreenMapping.model_validate(data).local_payload(screen)
    target = payload["steps"][0]["target"]
    assert payload["message_targets"] == [target]
    assert payload["completion_evidence"] == [{"condition_index": 0, "target": target}]
    assert {item["kind"] for item in screen["unresolved_annotations"]} == {
        "message_targets",
        "completion_evidence",
    }
    assert len(ScreenMatch.model_validate(data).steps) == 1
    data["steps"][0]["target"] = "unresolvable executable target"
    with pytest.raises(KeyError, match="unresolvable executable target"):
        ScreenMatch.model_validate(data)


def test_long_semantic_explanation_keeps_plan_and_original_mapping():
    explanation = "Visible save result and next action. " * 10
    response = ScreenMatch.model_validate(named_response(purpose=explanation))
    assert len(response.steps) == 1
    assert response.purpose == explanation[:160]
    assert response.mapping.purpose == explanation


@pytest.mark.parametrize("mode", ["enforce", "observe_only"])
def test_group_click_role_policy_respects_guard_mode(tmp_path, mode):
    from types import SimpleNamespace

    services = SimpleNamespace(
        settings=SimpleNamespace(guard_mode=mode),
        log=RunLog(tmp_path, case_id="group-click", agent_id="test"),
    )
    response = ScreenMatch.model_validate(
        {
            "screen_kind": "editor",
            "procedure_step": "task-action",
            "controls": [{"id": 1, "role": "group", "confidence": 1}],
            "steps": [{"target": 1, "action": "click", "intent": "navigate", "point": [10, 10]}],
        }
    )
    screen = {"markers": [{"id": 1, "source": "model_selected"}]}
    if mode == "enforce":
        with pytest.raises(ValueError, match="screen_match_action_surface_mismatch"):
            validate_action_surfaces(services, response, screen)
    else:
        assert validate_action_surfaces(services, response, screen) is response
        assert services.log.read_all()[-1].payload["guard"] == "action_control_role"


@pytest.mark.parametrize("operation", ["duplicate_clear", "duplicate_found"])
def test_query_result_is_remembered_then_next_action_is_selected(tmp_path, record, operation):
    first = named_response(
        screen_kind="query",
        operation=operation,
        steps=[],
        grid="Reference",
        row="Reference",
        evidence=[{"field": "hbl_no", "target": "Reference"}],
    )
    following = named_response()
    brain, model, *_ = setup_brain(tmp_path, record, [first, following])
    brain._observe()
    assert brain.phase == "task"
    assert brain._select_node() == "decision"
    assert brain.result is None
    assert brain.feedback[-1]["check"] == "query_result_observed"
    assert brain.feedback[-1]["operation"] == operation
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert len(model.prompts) == 2
    assert "query_result_observed" in model.prompts[-1]
    assert "Do not use duplicate_clear/duplicate_found" in model.prompts[-1]
