"""Current visual screen decisions share observations with the executor."""

import json
from dataclasses import replace
from hashlib import sha256
from itertools import repeat
from pathlib import Path

import pytest
from PIL import Image
from runtime_profile import current_settings
from test_brain import ScriptedModel
from test_brain import record as record
from test_visual_markers import fixture
from test_visual_scene import wire

import brain.graph.screen_decision as matching
from brain.cache import ScreenPatternCache, screen_pattern_key
from brain.graph.engine import Brain
from brain.graph.policy import DecisionPolicy, ExecutionFacts
from brain.graph.schemas import DecisionResponse
from brain.graph.screen_batch import LoginScreenPlan, ScreenFillPlan
from brain.graph.screen_decision import (
    ActionCapability,
    ScreenDecisionServices,
    ScreenMatch,
    ScreenSelection,
    bind_value_evidence_to_controls,
    build_decision_prompt,
    compile_match,
    compile_screen_action_plan,
    decide_screen,
    enforce_taught_input_surfaces,
    select_screen_response,
    task_action_contract,
    validate_action_surfaces,
    validate_match_plan,
)
from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES
from brain.task_instruction import TaskInstruction
from brain.visual.scene import SceneObservation
from sawon_runtime.storage import RunLog


@pytest.fixture
def task_instruction():
    return TaskInstruction.model_validate_json(
        (Path(__file__).resolve().parents[1] / "examples/shipment-task.json").read_bytes()
    )


@pytest.fixture
def decision_work_counts(monkeypatch):
    """Count real preparation calls without replacing their behavior."""
    counts = {}
    for name in (
        "prepare_screen_plan",
        "retain_screen_observation",
        "sanitize_unused_controls",
        "recall_context",
    ):
        original = getattr(matching, name)
        counts[name] = 0

        def counted(*args, _name=name, _original=original, **kwargs):
            counts[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(matching, name, counted)
    return counts


def test_ocr_fill_target_is_rejected_as_non_action_surface(tmp_path):
    class BrainStub:
        settings = current_settings(tmp_path)
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
        settings = current_settings(tmp_path)
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

        def read(self, page):
            self.calls += 1
            return boxes

    relay, ocr, detector = Relay(), Ocr(), Detector()
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="test")
    plans = iter(responses)

    def route(prompt):
        item = next(plans)
        if prompt.startswith(CONTEXT_MAPPING_RULES):
            assert "fields" in item, "Provider fixtures must use semantic names and coordinates"
        return item

    model = ScriptedModel(log, repeat(route))
    if task_instruction is None:
        task_instruction = TaskInstruction.model_validate_json(
            (Path(__file__).resolve().parents[1] / "examples/shipment-task.json").read_bytes()
        )
    brain = Brain(
        record=record,
        goal="Register synthetic shipment",
        relay=relay,
        model=model,
        settings=current_settings(tmp_path, task_instruction=task_instruction),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=ocr,
    )
    brain.screen_scanner.detector = detector
    return brain, model, relay, ocr, detector, meaning


def matching_contract(brain, screen=None):
    screen = screen or {"ocr": [], "markers": []}
    services = ScreenDecisionServices(
        settings=brain.settings,
        log=brain.log,
        call=brain._call,
        case_dir=brain.case_dir,
        cache=brain.visual_match_cache,
        memory_retrieval=brain.memory_retrieval,
    )
    return brain._screen_decision_request(screen, None), services


def remember_prior_inputs(brain, sources):
    """Use the real model-observation commit path for synthetic earlier screen readings."""
    from brain.graph.screen_mapping import ScreenMapping

    previous = brain.observation
    brain.observation = SceneObservation(wire=wire(), screen_kind="editor", elements=[])
    brain.observation_number += 1
    brain._remember_model_task_evidence(
        ScreenMatch(
            screen_kind="editor",
            procedure_step="task-action",
            controls=[],
            mapping=ScreenMapping(
                screen_path=["Earlier screen"],
                fields={
                    source: {
                        "source": source,
                        "role": "edit",
                        "bbox": [10, 20, 60, 15],
                        "value": brain.values[source],
                    }
                    for source in sources
                },
            ),
        )
    )
    brain.observation = previous


@pytest.mark.parametrize("last_action", ["wheel", "invalid-click"])
def test_synthetic_fill_then_wheel_crosses_point_binding_or_reports_undispatched_plan(
    tmp_path, record, last_action
):
    # Synthetic equivalent of 113's mixed proposal; uses the existing fake model/relay.
    from sawon_interfaces import UIElement

    brain, model, *_ = setup_brain(tmp_path, record, [])
    observed = wire()
    ref = "e" + str(observed.obs_id.int) + "01"
    brain.phase = "task"
    brain.observation = SceneObservation(
        wire=observed,
        screen_kind="editor",
        elements=[UIElement(ref=ref, role="edit", name="Reference", bbox=(20, 30, 200, 40))],
    )
    brain.visual_proposal_obs_id = observed.obs_id
    brain.visual_proposal = ScreenMatch(
        screen_kind="editor",
        controls=[],
        procedure_step="task-action",
        steps=[
            {
                "target": 1,
                "action": "fill",
                "intent": "fill",
                "source": "hbl_no",
                "point": [100, 50],
            },
            {
                "target": 1,
                "action": "key" if last_action == "wheel" else "click",
                "intent": "navigate",
                "keys": "pagedown" if last_action == "wheel" else None,
                "point": [300, 150],
            },
        ],
    )
    route = brain._select_node()
    if last_action == "wheel":
        assert route == "review"
        actions = [
            brain._wire_action(
                item.decision.action, item.decision, fresh_observation=observed, log_renewal=False
            ).root
            for item in brain.action_plan.actions
        ]
        assert [a.type for a in actions] == ["set_text", "scroll"]
        assert actions[1].target.image_point == (300, 150)
        assert actions[1].wheel_delta == -600
    else:
        assert route == "observation" and brain.action_plan is None
        request, services = matching_contract(brain)
        feedback = build_decision_prompt(request, services).state["notebook"]["recent_feedback"][-1]
        detail = feedback["validation_errors"][0]
        assert detail["loc"] == ["steps", 1, "point"]
        assert detail["target_name"] == "Reference"
        assert detail["target_bbox"] == [20, 30, 200, 40]
        assert detail["point"] == [300, 150] and detail["image_size"] == [400, 200]
        assert detail["plan_delivery"] == "not_dispatched" and detail["delivered_actions"] == 0
        assert "none of its actions" in feedback["required_response"]
    assert model.prompts == []
    assert not [e for e in brain.log.read_all() if e.kind == "act"]


@pytest.mark.parametrize("direction,delta", [("up", 600), ("down", -600)])
def test_synthetic_explicit_scroll_reaches_wire_without_preliminary_click(
    tmp_path, record, direction, delta
):
    # Synthetic 120: fake model, real observation/decision/coordinate/wire conversion.
    data = named_response(
        steps=[
            {
                "action": "scroll",
                "direction": direction,
                "target": "Reference",
                "intent": "navigate",
                "point": [300, 150],
            }
        ]
    )
    brain, model, *_ = setup_brain(tmp_path, record, [data])
    brain._observe()
    assert len(brain.visual_proposal.steps) == 1
    assert brain._select_node() == "review"
    action = brain._wire_action(
        brain.decision.action,
        brain.decision,
        fresh_observation=brain.observation.wire,
        log_renewal=False,
    ).root
    assert action.type == "scroll" and action.wheel_delta == delta
    assert action.target.image_point == (300, 150)  # Outside the field, inside the viewport.
    assert brain.action_plan is None
    assert len(model.prompts) == 1


@pytest.mark.parametrize(
    "action,extra",
    [
        ("click", {"keys": "pageup"}),
        ("click", {"direction": "up"}),
        ("scroll", {"direction": "up", "keys": "pageup"}),
        ("key", {"keys": "pageup"}),
    ],
)
def test_synthetic_click_scroll_mix_is_not_a_model_action(action, extra):
    from pydantic import ValidationError

    data = named_response(
        steps=[
            {
                "action": action,
                "target": "Reference",
                "intent": "navigate",
                "point": [300, 150],
                **extra,
            }
        ]
    )
    with pytest.raises(ValidationError):
        ScreenMatch.model_validate(data)


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
            return ScreenSelection(method_response, None, method_response, False, _prepared)
        return ScreenSelection(fallback_response, ("f" * 64, {}), None, False, _prepared)

    def ground(response, *_args, **_kwargs):
        if response is method_response:
            raise ValueError("input_label_not_near_control")
        return response

    rejected = []

    def reject_pending(*, reason):
        rejected.append(reason)
        return {"method_id": "method-1", "group_id": "group-1"}

    monkeypatch.setattr(matching, "select_screen_response", select)
    monkeypatch.setattr(matching, "validate_or_replan", lambda response, *_: response)
    monkeypatch.setattr(matching, "ground_screen_response", ground)
    monkeypatch.setattr(services.memory_retrieval, "reject_pending", reject_pending)

    result = decide_screen(request, services)

    assert result.response.purpose == "fallback"
    assert selections == [True, False]
    assert rejected == ["input_label_not_near_control"]
    event = next(
        item for item in brain.log.read_all() if item.payload.get("check") == "method_fallback"
    )
    assert event.payload["next"] == "visual_cache_or_model"


def response(meaning, **kwargs):
    """Internal scene fixture; provider replies use named_response instead."""
    return {
        **meaning.model_dump(),
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
        "fields": {
            "Reference": {
                "source": "hbl_no",
                "role": "edit",
                "bbox": [100, 15, 150, 30],
                "value": "",
            }
        },
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


@pytest.mark.parametrize("source", ["model", "method", "replanned"])
def test_synthetic_unchanged_plan_is_not_prepared_or_grounded_twice(
    tmp_path, record, decision_work_counts, source
):
    # Synthetic model/method input; all selection, correction and validation functions are real.
    data = named_response()
    invalid = named_response(
        fields={"Reference": {**data["fields"]["Reference"], "source": None}},
        steps=[{**data["steps"][0], "source": None}],
    )
    replies = [] if source == "method" else [invalid, data] if source == "replanned" else [data]
    brain, model, *_ = setup_brain(tmp_path, record, replies)
    request, services = matching_contract(brain)
    if source == "method":
        # A remembered input supplies its structure/source, never a current reading.
        del data["fields"]["Reference"]["value"]
        mapped = ScreenMatch.model_validate(data).mapping
        proposal = ScreenMatch.model_validate(mapped.local_payload(request.screen, request.values))
        request = replace(request, proposal=proposal.model_copy(update={"mapping": mapped}))

    result = decide_screen(request, services)

    assert [(step.action, step.source, step.point) for step in result.response.steps] == [
        ("fill", "hbl_no", [175, 30])
    ]
    assert decision_work_counts == {
        "prepare_screen_plan": 2 if source == "replanned" else 1,
        "retain_screen_observation": 1,
        "sanitize_unused_controls": 1,
        "recall_context": 0 if source == "method" else 1,
    }
    assert len(model.prompts) == {"method": 0, "model": 1, "replanned": 2}[source]
    if source == "replanned":
        assert any(
            event.payload.get("check") == "combined_action_corrected"
            for event in brain.log.read_all()
        )
    assert any(
        event.payload.get("check") == "match_context_size" for event in brain.log.read_all()
    ) is (source != "method")


def test_synthetic_deferred_context_preserves_prebinding_prompt_and_cache_key(
    tmp_path, record, monkeypatch
):
    # Synthetic method boundary mutates the same fields as real pattern binding;
    # an action-only memory then requires the normal model observation path.
    brain, model, *_ = setup_brain(tmp_path, record, [named_response()])
    request, services = matching_contract(brain)
    methods = services.memory_retrieval
    methods.enabled = True
    methods.state.purpose = "Before binding"
    eager_state = build_decision_prompt(request, services).state
    expected_key = screen_pattern_key(eager_state)
    expected_prompts = []
    contexts = []
    cache_keys = []
    recall = matching.recall_context
    services.cache.enabled = True

    def record_cache_key(state):
        key = screen_pattern_key(state)
        cache_keys.append(key)
        return key

    def select(screen):
        methods.state.purpose = "Newly bound group"
        methods.state.active_method_id = "synthetic-method"
        methods.state.bindings["synthetic-method"] = {"reference": "hbl_no"}
        screen["markers"].append(
            {"id": 99, "source": "omniparser", "bbox": [100, 15, 150, 30], "text_ids": []}
        )
        expected_prompts.append(matching.mapping_prompt(eager_state))
        return ScreenMatch(screen_kind="query", procedure_step="task-action", controls=[], steps=[])

    def capture_context(*args, **kwargs):
        context = recall(*args, **kwargs)
        contexts.append(context)
        return context

    monkeypatch.setattr(methods, "match_pattern", select)
    monkeypatch.setattr(matching, "recall_context", capture_context)
    monkeypatch.setattr(matching, "screen_pattern_key", record_cache_key)
    result = decide_screen(request, services)

    assert model.prompts == expected_prompts
    assert len(contexts) == 1
    assert json.dumps(contexts[0], ensure_ascii=False) == json.dumps(
        eager_state, ensure_ascii=False
    )  # Unsorted whole JSON also checks field order.
    assert result.visual_match_candidate[0] == expected_key
    assert cache_keys == [expected_key], "cache miss and promotion must share the same calculation"
    assert result.response.steps[0].source == "hbl_no"


def test_synthetic_current_field_source_is_not_reinferred_from_equal_input_value():
    from brain.graph.screen_mapping import ScreenMapping

    # Synthetic conflicting model annotations: only the declared field source owns the reading.
    mapping = ScreenMapping(
        fields={
            "Quantity": {
                "role": "edit",
                "bbox": [0, 0, 100, 20],
                "source": "unknown",
                "value": "12",
            },
            "Weight": {"role": "edit", "bbox": [0, 30, 100, 20], "source": "weight", "value": "20"},
        },
        evidence=[{"field": "packages.count", "target": "Quantity"}],
    )
    payload = mapping.local_payload(
        {}, {"packages.count": "12", "packages.unit": "CT", "weight": "20"}
    )
    assert [(item["field"], item["target"]) for item in payload["evidence"]] == [("weight", 2)]


@pytest.mark.parametrize(
    "target", ["12", "Quantity", "first row"], ids=["value", "substring", "role"]
)
def test_synthetic_completion_reference_is_not_guessed_from_value_name_or_role(target):
    from brain.graph.screen_mapping import ScreenMapping

    mapping = ScreenMapping(
        fields={"Quantity input": {"role": "row", "bbox": [0, 0, 100, 20], "value": "12"}},
        completion_evidence=[{"condition_index": 0, "target": target}],
    )
    screen = {}
    payload = mapping.local_payload(screen, {})
    assert payload["completion_evidence"] == []
    assert screen["unresolved_annotations"] == [{"kind": "completion_evidence", "target": target}]


def test_synthetic_model_refinement_request_survives_an_executable_action(tmp_path, record):
    # Existing fake provider, real decision path: defined actions do not cancel uncertainty.
    brain, model, *_ = setup_brain(tmp_path, record, [named_response(needs_refinement=True)])
    request, services = matching_contract(brain)
    result = decide_screen(request, services)
    assert result.response.needs_refinement is True
    assert result.response.steps[0].source == "hbl_no"
    assert len(model.prompts) == 1


def test_task_is_loaded_and_new_screen_match_is_one_combined_call(tmp_path, record):
    brain, model, _, _, _, _ = setup_brain(
        tmp_path, record, [named_response()], task_instruction=None
    )
    brain.credentials = {"credential.username": "secret-user", "credential.password": "secret-pw"}
    assert brain._plan_node() == "observation"
    assert not model.prompts
    assert json.loads((tmp_path / "task-instruction.json").read_text(encoding="utf-8"))["objective"]
    images_seen = []
    generate = model._generate

    def record_images(prompt, schema, *, images=None):
        images_seen.append(bool(images))
        return generate(prompt, schema, images=images)

    model._generate = record_images
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
    assert model.prompts[0].startswith(CONTEXT_MAPPING_RULES)
    assert brain.action_plan.actions[0].decision.action.source == "hbl_no"
    assert brain.action_plan.actions[0].decision.action.ref.startswith(
        "e" + str(brain.observation.obs_id.int)
    )
    assert "secret-user" not in model.prompts[0] and "secret-pw" not in model.prompts[0]
    assert images_seen == [True]


def test_named_current_evidence_is_bound_without_a_second_grounding_call(
    tmp_path, record, task_instruction
):
    data = named_response()
    data["fields"].update(
        {
            "Master": {
                "source": "mbl_no",
                "role": "text",
                "bbox": [10, 20, 60, 15],
                "value": record.fields.mbl_no.value,
            },
            "Shipper": {
                "source": "shipper",
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
    observed = {element.ref: element for element in brain.observation.elements}
    prefix = f"e{brain.observation.obs_id.int}0"
    for evidence in brain.visual_proposal.evidence:
        assert observed[prefix + str(evidence.target)].value == brain.values[evidence.field]
    assert brain._observed_inputs()["mbl_no"]["label"] == "Master"
    assert brain._observed_inputs()["shipper"]["label"] == "Shipper"


def test_task_instruction_exposes_goal_and_capabilities_without_fixed_ui_route(
    tmp_path, record, task_instruction
):
    task_response = named_response(
        procedure_step="로그인",
    )
    task_response["fields"]["Reference"]["value"] = record.fields.hbl_no.value
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
    remember_prior_inputs(brain, {"hbl_no"})

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
    assert state["notebook"]["remaining_inputs"] == sorted(set(brain.values) - {"hbl_no"})
    assert "record" not in state["notebook"]
    assert "remaining_fields" not in state["notebook"]
    persisted = json.loads((tmp_path / "task-notebook.json").read_text(encoding="utf-8"))
    assert persisted["inputs_path"] == "record.json"
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


def test_taught_direct_input_and_accepted_mapping_exclude_rejected_grid_write(
    tmp_path, record, monkeypatch
):
    from brain.graph.screen_mapping import ScreenMapping

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
    data = named_response(
        screen_kind="editor",
        taught_step_id="fill-record",
        fields={
            "House reference": {
                "role": "edit",
                "bbox": [10, 10, 80, 30],
                "source": "hbl_no",
            },
            "Shipper cell": {
                "role": "cell",
                "bbox": [10, 50, 80, 30],
                "source": "shipper",
                "value": "",
            },
            "Blank unbound": {
                "role": "edit",
                "bbox": [110, 50, 80, 30],
                "source": "",
                "value": "",
            },
            "Unobserved unbound": {"role": "edit", "bbox": [200, 50, 80, 30]},
            "Explicit null": {"role": "text", "bbox": [300, 50, 80, 30], "source": None},
            "Viewport": {"role": "group", "bbox": [0, 0, 400, 200]},
        },
        steps=[
            {
                "target": "House reference",
                "action": "fill",
                "intent": "fill",
                "point": [50.25, 25.75],
            },
            {
                "target": "Shipper cell",
                "action": "fill",
                "intent": "fill",
                "source": "shipper",
                "point": [50, 65],
            },
            {
                "target": "Viewport",
                "action": "scroll",
                "direction": "down",
                "intent": "navigate",
                "point": [300.25, 150.75],
            },
        ],
    )
    brain, model, *_ = setup_brain(tmp_path, record, [data], task_instruction=task_instruction)
    request, services = matching_contract(brain)
    remembered = []
    services.memory_retrieval.enabled = True
    monkeypatch.setattr(
        services.memory_retrieval,
        "remember",
        lambda _screen, accepted, **_: remembered.append(accepted),
    )
    result = decide_screen(request, services)
    filtered = result.response

    assert [(step.action, step.source, step.point) for step in filtered.steps] == [
        ("fill", "hbl_no", [50.25, 25.75]),
        ("key", None, [300.25, 150.75]),
    ]
    assert filtered.steps[1].keys == "pagedown"
    event = next(
        item
        for item in brain.log.read_all()
        if item.payload.get("check") == "taught_input_surface_mismatch_deferred"
    )
    assert event.payload["rejected"] == [
        {
            "source": "shipper",
            "target": next(
                c.id for c in filtered.controls if c.visual_description == "Shipper cell"
            ),
            "observed_surface": "table_cell",
            "allowed_surfaces": ["direct_input"],
        }
    ]
    assert len(model.prompts) == 1 and len(remembered) == 1
    stored = result.visual_match_candidate[1]["mapping"]
    assert remembered[0].mapping.model_dump(mode="json", exclude_unset=True) == stored
    assert stored["steps"] == [
        {**data["steps"][0], "source": "hbl_no"},
        data["steps"][2],
    ]
    assert stored["completion_evidence"] == stored["message_targets"] == []
    assert stored["grid"] is None
    # Keeping field presence is essential: omitted values are not observations of empty inputs.
    assert stored["fields"] == data["fields"]
    assert "value" not in stored["fields"]["House reference"]
    assert "source" not in stored["fields"]["Unobserved unbound"]
    assert stored["fields"]["Blank unbound"]["source"] == ""
    assert stored["fields"]["Blank unbound"]["value"] == ""
    replay = ScreenMapping.model_validate(stored).local_payload({}, request.values)
    assert [(step["action"], step.get("source"), step["point"]) for step in replay["steps"]] == [
        ("fill", "hbl_no", [50.25, 25.75]),
        ("key", None, [300.25, 150.75]),
    ]
    assert replay["steps"][1]["keys"] == "pagedown"
    assert "source" not in replay["steps"][1]
    assert replay["completion_evidence"] == replay["message_targets"] == []
    assert replay["grid"] is None


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
def test_taught_surface_compiles_and_dispatches_only_authorized_inputs(
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

    from brain.graph.screen_batch import ScreenFillPlan, prepare_screen_fill
    from brain.visual.markers import Marker, ground_meanings
    from brain.visual.scene import ControlMeaning, bind_scene

    if parent_role is not None:
        screen["markers"].append({"id": 6, "bbox": [0, 0, 180, 80], "source": "omniparser"})
        proposed.controls[0] = proposed.controls[0].model_copy(update={"parent_id": 6})
        proposed.controls.append(ControlMeaning(id=6, role=parent_role, confidence=1))
    markers = [Marker(**marker, confidence=1) for marker in screen["markers"]]
    brain.observation = bind_scene(
        wire(),
        [],
        ground_meanings(
            proposed, markers, [], allow_ocr_inputs=brain.settings.visual_model_led_semantics
        ),
    )
    plan = prepare_screen_fill(
        brain.observation, compile_match(proposed, brain.observation, ScreenFillPlan)
    )
    request, services = matching_contract(brain, screen)
    if not allowed:
        with pytest.raises(ValueError, match="task_surface_mismatch"):
            enforce_taught_input_surfaces(request, services, proposed)
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


def test_model_only_generic_task_keeps_model_grounded_input_progress(
    tmp_path, record, task_instruction
):
    task_response = named_response(procedure_step="Enter the visible task input")
    task_response["steps"] = []
    task_response["operation"] = "stop"
    task_response["evidence"] = [{"field": "hbl_no", "target": "Reference"}]
    task_response["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    brain, *_ = setup_brain(tmp_path, record, [task_response], task_instruction=task_instruction)

    brain._observe()
    assert brain._select_node() == "finish"

    assert "hbl_no" in brain._observed_inputs()
    event = next(
        event
        for event in brain.log.read_all()
        if event.payload.get("check") == "model_task_evidence_recorded"
    )
    assert event.payload["fields"] == ["hbl_no"]


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
                "source": "hbl_no",
                "role": "text",
                "bbox": [10, 20, 60, 15],
                "value": record.fields.hbl_no.value,
            }
        },
        evidence=[{"field": "hbl_no", "target": "Reference"}],
    )
    brain, model, *_ = setup_brain(tmp_path, record, [data], task_instruction=task_instruction)
    brain._observe()
    assert "hbl_no" in brain._observed_inputs()
    assert len(model.prompts) == 1


@pytest.mark.parametrize("visible", ["", "WRONG"], ids=["blank", "different"])
def test_observed_mismatch_replaces_claim_without_rejecting_repair(
    tmp_path, record, task_instruction, visible
):
    task_response = named_response()
    task_response["fields"]["Reference"]["value"] = visible
    task_response["evidence"] = [{"field": "hbl_no", "target": "Reference"}]
    brain, *_ = setup_brain(tmp_path, record, [task_response], task_instruction=task_instruction)
    remember_prior_inputs(brain, {"hbl_no"})
    brain._observe()
    assert "hbl_no" not in brain._observed_inputs()
    assert brain.visual_proposal.evidence == []
    assert brain._select_node() == "review"
    assert brain.action_plan.actions[0].decision.action.source == "hbl_no"


def test_task_capability_allows_save_after_prior_evidence_without_hiding_inputs(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "task"
    active = {"description", "hbl_no", "shipper"}
    remember_prior_inputs(brain, active)
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


def test_synthetic_requested_image_is_not_cancelled_by_defined_action_targets(tmp_path, record):
    # Synthetic cached response and fake call, exercising the real selection path.
    blocks, _, markers, meaning = fixture()
    brain, _, *_ = setup_brain(tmp_path, record, [])
    screen = {
        "ocr": [block.model_dump() for block in blocks],
        "markers": [marker.model_dump() for marker in markers],
    }
    request, services = matching_contract(brain, screen)
    initial = ScreenMatch.model_validate(response(meaning, needs_image=True))
    services.memory_retrieval.enabled = False
    calls = []

    def fake_call(prompt, schema, *, page=None):
        calls.append(prompt)
        return initial.model_copy(update={"needs_image": len(calls) == 1})

    services = replace(services, call=fake_call)
    result = select_screen_response(request, services, build_decision_prompt(request, services))
    assert len(calls) == 2
    assert result.response.needs_image is False
    assert result.response.steps == initial.steps


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
    task_response["steps"][0].pop("source" if action == "key" else "keys")
    task_response["evidence"] = [{"field": "hbl_no", "target": "Reference"}]
    brain, model, *_ = setup_brain(
        tmp_path, record, [task_response], task_instruction=task_instruction
    )

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


def test_promoted_exact_visual_match_reuse_precedes_model_call(
    tmp_path, record, task_instruction, monkeypatch
):
    cache_path = tmp_path / "visual-match.json"

    old_reading = named_response()
    old_reading["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    old_reading["repair_fields"] = ["Reference"]
    old_reading["observed_step_results"] = [
        {"step_id": "previous-read", "observed_result": "Previously visible", "completed": True}
    ]
    first, first_model, *_ = setup_brain(
        tmp_path / "first", record, [old_reading], task_instruction=task_instruction
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
    second.method_library.enabled = True
    recovery_permissions = []
    runtime_type = type(second.memory_retrieval)
    remember = runtime_type.remember

    def remember_cached(runtime, screen, response, **kwargs):
        recovery_permissions.append(kwargs.get("allow_recovery", True))
        return remember(runtime, screen, response, **kwargs)

    monkeypatch.setattr(runtime_type, "remember", remember_cached)
    second._observe()
    assert second_model.prompts == []
    assert "value" not in second.visual_proposal.mapping.fields["Reference"].model_fields_set
    assert second.visual_proposal.observed_step_results == []
    assert second.visual_proposal.mapping.repair_fields == []
    assert second.visual_proposal.evidence == []
    assert second._observed_inputs() == {}
    assert second._select_node() == "review"
    assert second_model.prompts == []
    hits = [
        event
        for event in second.log.read_all()
        if event.kind == "cache_hit" and event.payload.get("check") == "visual_match_cache"
    ]
    assert len(hits) == 1
    assert recovery_permissions == [False], "cached model facts must not authorize route recovery"


@pytest.mark.parametrize("has_review", [False, True], ids=["action-only", "current-save-review"])
def test_save_result_reuses_bound_review_or_requests_current_model_review(
    tmp_path, record, task_instruction, has_review, monkeypatch
):
    from brain.graph.screen_mapping import ScreenMapping

    replies = [] if has_review else [named_response(fields={}, steps=[], operation="stop")]
    brain, model, *_ = setup_brain(tmp_path, record, replies, task_instruction=task_instruction)
    request, services = matching_contract(brain)
    request = replace(request, phase="save_result")
    brain.memory_retrieval.enabled = True

    bound = []

    def bind_method(screen):
        bound.append(screen)
        return ScreenMatch(
            mapping=ScreenMapping(fields={}),
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

    services.memory_retrieval.match_pattern = bind_method
    cache = ScreenPatternCache(tmp_path / "save-result-cache.json", enabled=True)
    services = replace(services, cache=cache)
    prepared = build_decision_prompt(request, services)
    cached = ScreenMatch(
        mapping=ScreenMapping(fields={}, operation="stop"),
        screen_kind="dialog",
        controls=[],
        procedure_step="inspect-save",
        operation="stop",
        expected_result="Inspect the current save result",
    )
    cache.stage(
        screen_pattern_key(prepared.state),
        {**cached.model_dump(mode="json"), "mapping": cached.mapping.model_dump(mode="json")},
    )
    assert cache.finish(success=True) == 1

    def unused_lookup(key):
        pytest.fail("save-result review must not read a cache entry it will discard")

    monkeypatch.setattr(cache, "lookup", unused_lookup)

    selected = select_screen_response(request, services, prepared)

    if has_review:
        assert selected.response == selected.method_proposal
        assert selected.response.save_result == "ready_for_query"
        assert selected.response.steps[0].intent == "navigate"
        assert selected.cache_hit is None
        assert not selected.used_image
    else:
        assert selected.response.mapping.operation == "stop"
        assert selected.cache_hit is None
    assert bound == [request.screen]
    assert len(model.prompts) == (0 if has_review else 1)


def test_similar_capture_reruns_ocr_but_reuses_detector_geometry(tmp_path, record):
    next_action = named_response(
        fields={"Next": {"role": "button", "bbox": [100, 15, 150, 30]}},
        steps=[{"target": "Next", "action": "click", "intent": "navigate", "point": [175, 30]}],
    )
    brain, model, relay, ocr, detector, _ = setup_brain(
        tmp_path, record, [named_response(), next_action, next_action]
    )
    brain._observe()
    first_id = brain.observation.obs_id
    brain._observe()
    assert brain.observation.obs_id != first_id
    assert ocr.calls == 2 and detector.calls == 1
    # Similar-screen geometry reuse joins the same decision path during observation.
    assert len(model.prompts) == 2
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
    assert ocr.calls == 3 and detector.calls == 2


def test_task_partner_input_compiles_to_current_screen_batch(tmp_path, record):
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
    remember_prior_inputs(brain, {"hbl_no", "mbl_no", "packages.count", "packages.unit", "pol"})

    brain._observe()

    assert brain.observation.screen_kind == "partner"
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert len(brain.action_plan.actions) == 1
    assert brain.action_plan.actions[0].decision.intent == "fill"
    assert brain.action_plan.actions[0].decision.action.source == "shipper"


def test_observe_only_prefers_model_completion_over_an_attached_action(tmp_path, record):
    data = named_response(
        operation="complete",
        completion_evidence=[{"condition_index": 0, "target": "Reference"}],
    )
    data["steps"][0]["point"] = [9999, 9999]
    brain, *_ = setup_brain(tmp_path, record, [data])

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
    premature["fields"]["Reference"]["value"] = record.fields.hbl_no.value
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
    assert brain._select_node() == "review"
    assert brain._review_node() == "finish"
    assert brain.result.status == "success"
    complete = next(
        e.payload
        for e in brain.log.read_all()
        if e.payload.get("check") == "task_completion_fields"
    )
    assert set(complete["conditions"]) == set(instruction.completion_conditions)
    assert len(model.prompts) == 1


# Cleanup audit C4: preserve real refs and batch compilation without intermediate marker strings.
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
        DecisionPolicy(ExecutionFacts("task")),
    )


@pytest.mark.parametrize(
    "intents",
    [("navigate", "navigate"), ("select_option", "navigate")],
)
# Cleanup audit C4: preserve action order and current-frame targets.
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


def test_model_only_review_delegates_required_verification_without_repair_call(tmp_path, record):
    brain, model, *_ = setup_brain(tmp_path, record, [])
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

    request, services = matching_contract(brain)
    request = replace(request, required_verification=tuple(required))
    assert (
        matching.retain_screen_observation(
            matched, request, services, build_decision_prompt(request, services)
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
    screen = {
        "ocr": [block.model_dump() for block in blocks],
        "markers": [marker.model_dump() for marker in markers],
    }
    request, services = matching_contract(brain, screen)

    result = decide_screen(replace(request, post_action_review=True), services)

    assert len(model.prompts) == 1
    assert result.response.operation == "act"
    expected = reviewed["steps"][0]
    actual = result.response.steps[0]
    assert (actual.action, actual.source, actual.point) == (
        expected["action"],
        expected["source"],
        expected["point"],
    )
    assert result.response.needs_refinement is False


def test_synthetic_mappingless_method_requires_current_model_observation(tmp_path, record):
    blocks, _, markers, meaning = fixture()
    brain, model, *_ = setup_brain(tmp_path, record, [named_response()])
    screen = {
        "ocr": [block.model_dump() for block in blocks],
        "markers": [marker.model_dump() for marker in markers],
    }
    request, services = matching_contract(brain, screen)
    replay = ScreenMatch.model_validate(response(meaning))
    services.memory_retrieval.match_pattern = lambda _screen: replay

    result = decide_screen(replace(request, post_action_review=True), services)

    assert result.response.operation == replay.operation
    assert result.response.mapping.fields["Reference"].source == "hbl_no"
    assert len(model.prompts) == 1
    assert any(
        e.payload.get("reason") == "missing_screen_memory"
        for e in brain.log.read_all()
        if e.payload.get("check") == "memory_retrieval_exception"
    )


def test_synthetic_mappingless_cache_requires_current_model_observation(tmp_path, record):
    blocks, _, markers, meaning = fixture()
    current = named_response()
    current["fields"]["Reference"]["value"] = "WRONG"
    brain, model, *_ = setup_brain(tmp_path, record, [current])
    screen = {
        "ocr": [block.model_dump() for block in blocks],
        "markers": [marker.model_dump() for marker in markers],
    }
    request, services = matching_contract(brain, screen)
    request = replace(request, post_action_review=True)
    services.memory_retrieval.enabled = False
    services = replace(
        services,
        cache=ScreenPatternCache(tmp_path / "post-action-cache.json", enabled=True),
    )
    prepared = build_decision_prompt(request, services)
    cached = ScreenMatch.model_validate(response(meaning))
    services.cache.stage(screen_pattern_key(prepared.state), cached.model_dump(mode="json"))
    assert services.cache.finish(success=True) == 1

    result = decide_screen(request, services)

    assert result.response.mapping.fields["Reference"].source == "hbl_no"
    assert result.response.mapping.fields["Reference"].value == "WRONG"
    assert result.response.evidence == []
    assert result.response.steps[0].source == "hbl_no"
    assert len(model.prompts) == 1
    assert not any(
        event.kind == "cache_hit" and event.payload.get("check") == "visual_match_cache"
        for event in brain.log.read_all()
    )


@pytest.mark.parametrize(
    "review",
    [
        "batch",
        "delivered-input",
        "pending-memory",
        "method-complete",
        "unmatched-query",
        "cache-complete",
        "cache-duplicate-clear",
        "cache-duplicate-found",
        "cache-stop",
    ],
)
def test_synthetic_bound_method_precedes_review_gates_without_inventing_current_values(
    tmp_path, record, review
):
    # Synthetic stored method/cache and fake model; selection and prompt assembly are real.
    from brain.graph.screen_mapping import ScreenMapping

    current = named_response(
        fields={
            "Reference": {
                "source": "hbl_no",
                "role": "edit",
                "bbox": [100, 15, 150, 30],
                "value": record.fields.hbl_no.value,
            },
            "Continue": {"role": "button", "bbox": [250, 15, 80, 30]},
        },
        steps=[{"target": "Continue", "action": "click", "intent": "navigate", "point": [280, 30]}],
    )
    brain, model, *_ = setup_brain(tmp_path, record, [current])
    request, services = matching_contract(brain)
    request.screen["observation_route"] = "memory_retrieval"
    mapping = ScreenMapping.model_validate(
        {
            **current,
            "fields": {
                name: {k: v for k, v in item.items() if k != "value"}
                for name, item in current["fields"].items()
            },
        }
    )
    if review == "method-complete":
        mapping = ScreenMapping.model_validate(
            {
                **mapping.model_dump(exclude_unset=True),
                "operation": "complete",
                "steps": [],
                "completion_evidence": [{"condition_index": 0, "target": "Reference"}],
            }
        )
    method = ScreenMatch.model_validate(mapping.local_payload(request.screen, request.values))
    method = method.model_copy(update={"mapping": mapping})
    if review == "batch":
        request = replace(request, post_action_review=True)
    elif review in {"delivered-input", "unmatched-query"}:
        request = replace(
            request,
            required_verification=(
                {
                    "source": "hbl_no",
                    "screen": "query",
                    "descriptor": {"role": "edit", "name": "Reference", "label": "Reference"},
                    "input_region": [100, 15, 150, 30],
                },
            ),
        )
    elif review == "pending-memory":
        request.screen["memory_fields"] = [
            {"name": name, **item.model_dump(exclude_unset=True)}
            for name, item in mapping.fields.items()
        ]
        request = replace(
            request,
            screen_nodes={
                "[]": {
                    "path": [],
                    "controls": {},
                    "fields": {
                        "hbl_no": {
                            "label": "Reference",
                            "role": "edit",
                            "bbox": [100, 15, 150, 30],
                            "delivery": {
                                "value": record.fields.hbl_no.value,
                                "observation_number": 2,
                            },
                        }
                    },
                }
            },
        )
    request = replace(
        request,
        grounded_interpretation=None if review == "unmatched-query" else method.interpretation(),
    )
    bound_screens = []

    def bind_method(screen):
        bound_screens.append(screen)
        return None if review.startswith("cache-") or review == "unmatched-query" else method

    services.memory_retrieval.match_pattern = bind_method
    services = replace(
        services, cache=ScreenPatternCache(tmp_path / "review-cache.json", enabled=True)
    )
    prepared = build_decision_prompt(request, services)
    stale = ScreenMatch.model_validate(current)
    stale.mapping.fields["Reference"] = stale.mapping.fields["Reference"].model_copy(
        update={"value": "PAST-RUN"}
    )
    if review.startswith("cache-"):
        stale = stale.model_copy(
            update={"operation": review.removeprefix("cache-").replace("-", "_"), "steps": []}
        )
    if review != "unmatched-query":
        services.cache.stage(
            screen_pattern_key(prepared.state),
            {**stale.model_dump(mode="json"), "mapping": stale.mapping.model_dump(mode="json")},
        )
        assert services.cache.finish(success=True) == 1

    result = decide_screen(request, services)

    expected_steps = [] if review == "method-complete" else [("click", [280, 30])]
    assert [(step.action, step.point) for step in result.response.steps] == expected_steps
    assert bound_screens == [request.screen]
    assert not any(e.payload.get("check") == "visual_match_cache" for e in brain.log.read_all())
    exceptions = [
        e.payload["reason"]
        for e in brain.log.read_all()
        if e.payload.get("check") == "memory_retrieval_exception"
    ]
    if not review.startswith("cache-") and review != "unmatched-query":
        assert model.prompts == []
        assert result.response.operation == ("complete" if review == "method-complete" else "act")
        assert "value" not in result.response.mapping.fields["Reference"].model_fields_set
        assert result.response.evidence == []
        assert result.response.observed_step_results == []
        assert exceptions == []
    else:
        assert len(model.prompts) == 1
        assert result.response.mapping.fields["Reference"].value == record.fields.hbl_no.value
        assert [item.field for item in result.response.evidence] == ["hbl_no"]
        assert exceptions == ["current_values_not_observed"]


def test_synthetic_unseen_pending_input_does_not_interrupt_structural_navigation(tmp_path, record):
    # Synthetic prior-screen delivery; only the current navigation target is visible.
    from brain.graph.screen_memory import pending_screen_inputs

    brain, model, *_ = setup_brain(tmp_path, record, [])
    request, services = matching_contract(brain)
    nodes = {
        '["Previous"]': {
            "path": ["Previous"],
            "controls": {},
            "fields": {
                "hbl_no": {
                    "label": "Reference",
                    "role": "edit",
                    "bbox": [100, 15, 150, 30],
                    "delivery": {
                        "value": record.fields.hbl_no.value,
                        "observation_number": 2,
                    },
                }
            },
        }
    }
    original = json.dumps(nodes, ensure_ascii=False)
    mapped = ScreenMatch.model_validate(
        named_response(
            screen_path=["Current"],
            fields={"Continue": {"role": "button", "bbox": [250, 15, 80, 30]}},
            steps=[
                {"target": "Continue", "action": "click", "intent": "navigate", "point": [280, 30]}
            ],
        )
    ).mapping
    request.screen["memory_fields"] = [
        {"name": name, **item.model_dump(exclude_unset=True)}
        for name, item in mapped.fields.items()
    ]
    bound = ScreenMatch.model_validate(mapped.local_payload(request.screen, request.values))
    request = replace(request, screen_nodes=nodes)
    services.memory_retrieval.match_pattern = lambda _screen: bound.model_copy(
        update={"mapping": mapped}
    )

    result = decide_screen(request, services)

    assert model.prompts == []
    assert [(step.action, step.point) for step in result.response.steps] == [("click", [280, 30])]
    assert pending_screen_inputs(nodes) == {"hbl_no"}
    assert json.dumps(nodes, ensure_ascii=False) == original


# Cleanup audit C6: retain necessary preparation of a changed recovery response.
def test_empty_action_recovery_preserves_education_and_current_observed_values(
    tmp_path, record, decision_work_counts
):
    initial = named_response(steps=[])
    initial["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    recovered = named_response(
        fields={**initial["fields"], "Continue": {"role": "button", "bbox": [250, 15, 80, 30]}},
        steps=[{"target": "Continue", "action": "click", "intent": "navigate", "point": [280, 30]}],
    )
    taught = TaskInstruction.model_validate_json(
        (
            Path(__file__).resolve().parents[1] / "examples/shipment-user-taught-task.json"
        ).read_bytes()
    )
    brain, model, *_ = setup_brain(tmp_path, record, [initial, recovered], task_instruction=taught)
    brain._observe()
    assert len(model.prompts) == 2
    first, second = [json.loads(p[len(CONTEXT_MAPPING_RULES) :]) for p in model.prompts]
    assert second["task"] == first["task"]
    assert second["taught_procedure_progress"] == first["taught_procedure_progress"]
    assert second["recovery"]["matched_inputs"] == ["hbl_no"]
    assert "hbl_no" in brain._observed_inputs()
    assert brain.visual_proposal.mapping.fields["Reference"].value == record.fields.hbl_no.value
    assert brain.visual_proposal.steps[0].intent == "navigate"
    assert brain.visual_proposal.mapping.steps[0].target == "Continue"
    assert decision_work_counts == {
        "prepare_screen_plan": 2,
        "retain_screen_observation": 2,
        "sanitize_unused_controls": 2,
        "recall_context": 1,
    }


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
    duplicate = result.model_copy(
        update={"operation": "duplicate_clear", "row": None, "evidence": []}
    )
    assert compile_match(duplicate, scene, DecisionResponse).to_decision().grid_ref == prefix + "1"


def test_selection_row_and_confirmation_compile_to_existing_executor():
    from types import SimpleNamespace
    from uuid import uuid4

    from brain.graph.screen_decision import compile_screen_action_plan

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

    from brain.graph.screen_decision import compile_screen_action_plan

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
    from brain.graph.screen_decision import ActionCapability, bind_selection_effect

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
    from brain.observe.summary import describe

    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "task"

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
        "effect_source": "shipper",
    }

    def observe():
        brain.observation = editor
        return editor

    brain._observe = observe
    assert brain._verify_node() == "review"
    assert "shipper" in brain._observed_inputs()
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
    brain._decide_visual_screen(
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
    remember_prior_inputs(brain, set(brain.values) - {"consignee"})
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

    brain._decide_visual_screen(
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
    assert "consignee" not in brain._observed_inputs()
    assert "procedure" not in state


def test_observe_only_records_policy_scope_and_disabled_target_without_rejecting(tmp_path, record):
    from case_labels import labeled_case
    from test_brain import element

    from brain.graph.schemas import Choice, Decision

    brain, *_ = setup_brain(tmp_path, record, [])
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
    # The execution guard still emits diagnostics in current mode; it does not
    # reselect the input or raise an enforce-mode rejection.
    cases = (
        ("disabled", "Edit", False, "target_not_current_or_enabled"),
        ("non_input", "Button", True, "target_not_text_input"),
        ("wrong_scope", "Cell", True, "record_input_scope_mismatch"),
    )
    for label, role, enabled, rejection in cases:
        with labeled_case(label):
            target = element("e1", role, name=label, enabled=enabled)
            brain.observation = SceneObservation(
                wire=wire(), screen_kind="editor", elements=[target]
            )
            proposed = Decision(
                screen_kind="editor",
                operation="act",
                intent="fill",
                subgoal="Fill the current input",
                reason="Synthetic input guard diagnostic",
                action=Choice(type="set_text", ref="e1", source="hbl_no"),
            )

            assert brain._guard_action(proposed) is None
            assert brain.log.read_all()[-1].payload == {
                "check": "guard_observe_only",
                "guard": "action",
                "would_reject": rejection,
                "intent": "fill",
                "action": "set_text",
                "ref": "e1",
            }


def test_pending_fields_remain_evidence_context_without_claiming_delivery(tmp_path, record):
    from brain.graph.schemas import Choice, Decision

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
    remember_prior_inputs(brain, set(brain.values) - {"hbl_no", "shipper"})
    brain.decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="fill",
        subgoal="fill-entry",
        reason="fill pending field",
        action=Choice(type="set_text", ref="e1", source="hbl_no"),
    )
    brain.pending = {"decision": brain.decision}

    brain._decide_visual_screen(
        {
            "ocr": [block.model_dump() for block in fixture()[0]],
            "markers": [marker.model_dump() for marker in fixture()[2]],
        },
        None,
    )

    state = json.loads(model.prompts[-1][len(CONTEXT_MAPPING_RULES) :])
    assert state["notebook"]["pending_verification_inputs"] == ["hbl_no"]
    assert set(state["notebook"]["remaining_inputs"]) == {"hbl_no", "shipper"}
    assert "hbl_no" not in brain._observed_inputs()
    assert "hbl_no" in state["capability"]["input_scope"]
    assert "procedure" not in state


@pytest.mark.parametrize("dependency", ["unrelated", "dangling_unused", "action", "direct_proof"])
def test_only_nonproof_invalid_parent_links_are_omitted(tmp_path, record, dependency):
    from brain.graph.screen_decision import sanitize_unused_controls
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
    scene = bind_scene(
        wire(),
        blocks,
        ground_meanings(
            meanings, markers, blocks, allow_ocr_inputs=brain.settings.visual_model_led_semantics
        ),
    )
    result_count = next(element for element in scene.elements if element.value == "조회 결과 0건")
    assert result_count.parent_ref is None


def test_control_cleanup_keeps_unbound_current_evidence_for_compilation_error(tmp_path, record):
    from brain.graph.screen_decision import sanitize_unused_controls
    from brain.visual.markers import Marker, ground_meanings
    from brain.visual.scene import SemanticDetection, bind_scene

    brain, *_ = setup_brain(tmp_path, record, [])
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
    scene = bind_scene(
        wire(),
        [],
        ground_meanings(
            meanings, markers, [], allow_ocr_inputs=brain.settings.visual_model_led_semantics
        ),
    )
    with pytest.raises(ValueError, match="visual_proposal_unknown_marker"):
        compile_match(response, scene, DecisionResponse)
    checks = {event.payload.get("check") for event in brain.log.read_all()}
    assert "unbound_action_evidence_omitted" not in checks
    assert "stale_verified_evidence_omitted" not in checks
    assert brain._observed_inputs() == {}


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


# Cleanup audit C2: preserve expanded education in the recovery request.
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


# Cleanup audit C2: preserve the same context on fallback.
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
    from brain.graph.screen_mapping import ScreenMapping

    proposal = proposal.model_copy(update={"mapping": ScreenMapping(fields={})})
    request, services = matching_contract(brain, screen)

    result = decide_screen(replace(request, proposal=proposal), services)

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


def test_compound_claim_survives_before_action_without_becoming_verified(tmp_path, record):
    data = named_response(operation="duplicate_clear")
    brain, model, *_ = setup_brain(tmp_path, record, [data])
    brain._observe()
    assert brain.visual_proposal.operation == "act"
    assert brain.visual_proposal.steps
    assert brain.duplicate_clear is False
    claim = next(item for item in brain.feedback if item.get("check") == "model_observation_claim")
    assert claim["operation"] == "duplicate_clear"
    assert claim["status"] == "reported_not_independently_verified"
    assert len(model.prompts) == 1
    assert not any(event.kind == "act" for event in brain.log.read_all())


def test_action_owned_control_normalizes_once_and_keeps_cache_contract():
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
    # All supporting metadata takes the same replacement path. One collision
    # exercises every field; provider field ordering is covered by the memory replay.
    raw["controls"] = [
        {
            **control,
            "role": "button",
            "confidence": 0.1,
            "visual_description": "Supporting observation. " * 20,
        }
    ]
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
    brain._observe()
    assert brain._select_node() == "review"
    selected = brain.action_plan.actions[0].decision
    wired = brain._wire_action(selected.action, selected)
    assert wired.root.target.image_point == (175.25, 30.75)
    assert wired.root.target.evidence.startswith("image_planned:")
    assert selected.action.source == "hbl_no"
    assert len(model.prompts) == 1  # No target-semantics supplement or coordinate-repair call.


# Cleanup audit C3: keep actual-frame mapping while removing the empty-screen conversion.
def test_semantic_model_mapping_becomes_observation_and_coordinate_method(
    tmp_path, record, monkeypatch
):
    from brain.cache.methods import MethodLibrary, method_from_match
    from brain.graph.screen_mapping import ScreenMapping
    from brain.visual.action_rebind import perceptual_hash
    from pipeline.schemas import OcrBlock

    converted_frames = []
    original_payload = ScreenMapping.local_payload

    def convert(mapping, screen, inputs=None):
        converted_frames.append(screen)
        return original_payload(mapping, screen, inputs)

    monkeypatch.setattr(ScreenMapping, "local_payload", convert)
    raw = {
        "screen_kind": "query",
        "fields": {
            "Reference": {
                "source": "hbl_no",
                "role": "edit",
                "bbox": [100, 15, 150, 30],
                "value": "CURRENT",
            }
        },
        "steps": [{"target": "Reference", "action": "fill", "intent": "fill", "point": [125, 28]}],
    }
    brain, model, _, ocr, _, _ = setup_brain(tmp_path, record, [raw])
    blocks = fixture()[0]
    monkeypatch.setattr(
        ocr,
        "read",
        lambda _page: [
            *blocks,
            OcrBlock(id=9, text="CURRENT", bbox=(110, 20, 70, 15), confidence=1),
        ],
    )
    brain._observe()
    assert len(converted_frames) == 1 and converted_frames[0]["markers"]
    target = next(e for e in brain.observation.elements if e.role == "edit")
    assert target.name == "Reference" and target.bbox == (100, 15, 150, 30)
    assert brain._select_node() == "review"
    assert brain.action_plan.actions[0].decision.action.source == "hbl_no"
    # Raw provider artifact is semantic, executor refs are generated afterwards.
    artifact = next((tmp_path / "matching").glob("*-semantic-mapping.json"))
    assert (
        json.loads(artifact.read_text(encoding="utf-8"))["fields"]["Reference"]["source"]
        == "hbl_no"
    )
    # The final provider prompt keeps current OCR connected to the detector box.
    assert model.prompts[0].startswith("Choose the next screen action")
    for stage in ("Scanning", "Pattern Matching", "Memory Retrieval"):
        assert stage in model.prompts[0], stage
    assert '"id_namespace"' not in model.prompts[0]
    prompted_frame = json.loads(model.prompts[0][len(CONTEXT_MAPPING_RULES) :])["screen"]
    assert prompted_frame["text_regions"] == [
        {"text": "Reference", "bbox": [10, 20, 60, 15]},
        {"text": "CURRENT", "bbox": [110, 20, 70, 15]},
    ]
    assert prompted_frame["control_regions"] == [
        {
            "bbox": [100, 15, 150, 30],
            "contained_text": [{"text": "CURRENT", "bbox": [110, 20, 70, 15]}],
        }
    ]
    schema = ScreenMatch.model_json_schema()
    assert schema["$defs"]["NamedAction"]["properties"]["target"]["type"] == "string"
    screen = brain.screen_scanner.planning_context()[0].screen
    mapping = ScreenMapping.model_validate(raw)
    payload = mapping.local_payload(screen)
    original_markers = json.dumps(screen["markers"], ensure_ascii=False)
    assert mapping.local_payload(screen) == payload
    assert json.dumps(screen["markers"], ensure_ascii=False) == original_markers
    assert len([m for m in screen["markers"] if m["source"] == "model_selected"]) == 1
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
    bound = library.bind_with_context(
        learned, {"hbl_no": "next_number"}, fresh, {"next_number": "NEW-2"}
    )
    assert bound is not None
    assert bound.response["steps"][0]["point"] == [125, 28]
    assert bound.response["steps"][0]["source"] == "next_number"
    assert len(model.prompts) == 1
    # A different frame or viewport must yield back to model selection.
    for change in (
        {"screen_phash": screen["screen_phash"] ^ ((1 << 64) - 1)},
        {"image_size": [800, 400]},
    ):
        assert (
            library.bind_with_context(learned, {}, {**fresh, **change}, {"hbl_no": "NEXT"}) is None
        )
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
                    "source": "hbl_no",
                    "role": "edit",
                    "bbox": [100, 15, 150, 30],
                    "value": "HBL-23",
                }
            },
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
def test_review_evidence_uses_only_declared_field_sources(operation):
    from brain.graph.screen_mapping import ScreenMapping

    inputs = {
        "shipper": "Sender",
        "consignee": "Receiver",
        "mbl_no": "MBL-1",
        "left": "shared",
        "right": "shared",
    }
    visible = {
        "송하인": ("shipper", "Sender"),
        "Consignee": ("consignee", "Receiver"),
        "MBL No.": ("mbl_no", "MBL-1"),
        "Unknown": (None, "not supplied"),
        "Ambiguous": (None, "shared"),
    }
    mapping = ScreenMapping(
        fields={
            name: {"source": source, "role": "edit", "bbox": [0, 0, 100, 20], "value": value}
            for name, (source, value) in visible.items()
        },
        evidence=[{"field": name, "target": name} for name in visible],
        operation=operation,
    )
    screen = {}
    response = ScreenMatch.model_validate(mapping.local_payload(screen, inputs))
    assert [item.field for item in response.evidence] == ["shipper", "consignee", "mbl_no"]
    assert response.operation == operation
    assert screen["unresolved_annotations"] == []
    ExecutionFacts(
        phase="task",
        field_names=tuple(inputs),
        verified_field_names=tuple(item.field for item in response.evidence),
    )
    mapping.fields["Ambiguous"].source = "right"
    assert (
        ScreenMatch.model_validate(mapping.local_payload({}, inputs)).evidence[-1].field == "right"
    )


# Cleanup audit C1/C5: historical annotations cannot override field-derived evidence.
@pytest.mark.parametrize("source", ["packages.count", None], ids=["bound-reading", "unassigned"])
def test_saved_mapping_ignores_retired_evidence_and_preserves_field_readings(source):
    from brain.graph.screen_mapping import ScreenMapping

    raw = {
        "fields": {
            "수량": {"role": "edit", "bbox": [120, 193, 451, 32], "source": source, "value": "12"}
        },
        "evidence": [{"field": "description", "target": "absent target"}],
        "repair_fields": ["additional destination value"],
    }
    mapping = ScreenMapping.model_validate(raw)
    payload = mapping.local_payload({})
    assert [item["field"] for item in payload["evidence"]] == ([source] if source else [])
    assert payload["repair_fields"] == []
    assert "evidence" not in mapping.model_dump()
    assert "evidence" not in ScreenMatch.model_json_schema()["properties"]
    assert (
        ScreenMapping.model_validate({k: v for k, v in raw.items() if k != "evidence"}) == mapping
    )
    with pytest.raises(ValueError):
        ScreenMapping.model_validate({**raw, "unknown_contract_field": True})


def test_semantic_mapping_uses_explicit_result_names_for_completion():
    from brain.graph.screen_mapping import ScreenMapping

    mapping = ScreenMapping.model_validate(
        {
            "screen_kind": "query",
            "fields": {
                "조회 결과": {"role": "table", "bbox": [30, 160, 1100, 500]},
                "저장된 결과 행": {"role": "row", "bbox": [30, 170, 1100, 45]},
            },
            "operation": "complete",
            "grid": "조회 결과",
            "row": "저장된 결과 행",
            "completion_evidence": [
                {"condition_index": 0, "target": "저장된 결과 행"},
                {"condition_index": 1, "target": "저장된 결과 행"},
            ],
        }
    )

    response = ScreenMatch.model_validate(mapping.local_payload({}))

    assert response.grid is not None
    assert response.row is not None
    assert {item.target for item in response.completion_evidence} == {response.row}


def test_semantic_mapping_never_retargets_actions_from_a_descriptive_fragment():
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

    with pytest.raises(KeyError, match="Save Completion Close"):
        mapping.local_payload({})
    mapping.steps[0].target = "Save Completion Close Button"
    response = ScreenMatch.model_validate(mapping.local_payload({}))
    assert response.steps[0].target != response.grid
    assert response.grid is None  # An act plan does not consume result-grid metadata.


@pytest.mark.parametrize("separate", [False, True], ids=["combined", "reused-observation"])
def test_named_fields_replace_number_recovery_for_mixed_targets(tmp_path, record, separate):
    first = named_response()
    first["fields"]["Reference"]["source"] = "mbl_no"
    data = named_response(
        fields={
            "Reference": {"source": "hbl_no", "role": "edit", "bbox": [100, 15, 150, 30]},
            "New reference": {"source": "mbl_no", "role": "edit", "bbox": [100, 90, 150, 30]},
        },
        steps=[
            {"target": "Reference", "action": "fill", "intent": "fill", "point": [125, 28]},
            {"target": "New reference", "action": "fill", "intent": "fill", "point": [126, 103]},
        ],
    )
    replies = [first, data] if separate else [data]
    brain, model, *_ = setup_brain(tmp_path, record, replies)
    brain._observe()
    if separate:
        # Replan on the same capture; a corrected source replaces the old binding.
        brain.visual_proposal = None
    assert brain._select_node() == "review"
    assert [a.decision.action.source for a in brain.action_plan.actions] == ["hbl_no", "mbl_no"]
    assert [
        brain.visual_points.get(brain.observation, a.decision).point
        for a in brain.action_plan.actions
    ] == [(125, 28), (126, 103)]
    selected = brain.screen_scanner._catalog.selected_fields.values()
    assert {item["name"]: item["source"] for item in selected} == {
        "Reference": "hbl_no",
        "New reference": "mbl_no",
    }
    assert len(model.prompts) == len(replies)


def test_named_missing_target_defers_plan_without_number_grounding_retry(tmp_path, record):
    data = named_response()
    data["steps"][0]["target"] = "Missing field"
    brain, model, *_ = setup_brain(tmp_path, record, [data])
    brain._observe()
    assert brain.visual_plan_error == "screen_match_action_target_undeclared"
    assert len(model.prompts) == 1
    assert not any(e.kind == "act" for e in brain.log.read_all())


@pytest.mark.parametrize("terminal", ["search", "save", "navigate"])
def test_named_input_then_command_executes_and_observes_once(tmp_path, record, terminal):
    from sawon_interfaces import ActionResult

    data = named_response(
        fields={
            "Reference": {"source": "hbl_no", "role": "edit", "bbox": [100, 15, 150, 30]},
            "Command": {"role": "button", "bbox": [270, 15, 90, 30]},
        },
        steps=[
            {"target": "Reference", "action": "fill", "intent": "fill", "point": [125, 28]},
            {"target": "Command", "action": "click", "intent": terminal, "point": [310, 28]},
        ],
    )
    after = named_response(operation="stop", steps=[], evidence=[])
    brain, model, relay, *_ = setup_brain(tmp_path, record, [data, after])
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
    assert "hbl_no" not in brain._observed_inputs()
    assert len(brain.pending["execution"].delivered) == 2
    assert brain._verify_node() == "review"
    assert len(model.prompts) == 2
    assert brain.pending is None
    assert "hbl_no" not in brain._observed_inputs()


@pytest.mark.parametrize("failure_index", [0, 1], ids=["input-fails", "command-fails"])
def test_named_mixed_plan_failure_records_only_delivered_prefix(tmp_path, record, failure_index):
    from sawon_interfaces import ActionResult

    data = named_response(
        fields={
            "Reference": {"source": "hbl_no", "role": "edit", "bbox": [100, 15, 150, 30]},
            "Command": {"role": "button", "bbox": [270, 15, 90, 30]},
        },
        steps=[
            {"target": "Reference", "action": "fill", "intent": "fill", "point": [125, 28]},
            {"target": "Command", "action": "click", "intent": "search", "point": [310, 28]},
        ],
    )
    brain, model, relay, *_ = setup_brain(tmp_path, record, [data])
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
    assert "hbl_no" not in brain._observed_inputs()
    assert any(e.payload.get("reason") == "relay_action_failed" for e in brain.log.read_all())


# Cleanup audit C2: preserve final review and education context when removing prompt round trip.
def test_mapping_preserves_review_and_retrieval_purpose_and_ignores_unused_grid():
    from brain.graph.screen_mapping import ScreenMapping, mapping_prompt
    from brain.visual.frame import frame_input

    state = {
        "screen": {},
        "post_action_review": {"delivered_sources": ["hbl_no"]},
        "taught_procedure_progress": {"retrieved_step_ids": ["details"]},
    }
    stored = {
        "action": "key",
        "keys": "pageup",
        "target": "Reference",
        "source": None,
        "intent": "navigate",
        "point": [300, 150],
    }
    screen = {"memory_fields": ["Reference"], "retrieved_procedure": [{"steps": [stored]}]}
    state["screen"] = frame_input(screen)
    prompt = mapping_prompt(state)
    payload = json.loads(prompt[len(CONTEXT_MAPPING_RULES) :])
    assert "already delivered" in payload["review_instruction"]
    assert "not another retrieval" in payload["education_instruction"]
    recalled = payload["screen"]["retrieved_procedure"][0]["steps"][0]
    assert recalled["action"] == "scroll" and recalled["direction"] == "up"
    assert "keys" not in recalled and "source" not in recalled
    assert stored["action"] == "key" and stored["keys"] == "pageup"
    mapping = ScreenMapping.model_validate(named_response(grid="nonexistent grid", row="absent"))
    assert mapping.local_payload({})["grid"] is None
    assert len(mapping.local_payload({})["steps"]) == 1


@pytest.mark.parametrize("rejected_next_plan", [False, True], ids=["valid-plan", "rejected-plan"])
def test_current_observation_survives_rejected_next_plan(
    tmp_path, record, monkeypatch, rejected_next_plan
):
    """Synthetic mapping: commit current readings even when its next action cannot compile."""
    task_response = named_response(
        screen_kind="editor",
        steps=[],
        operation="stop",
        evidence=[{"field": "hbl_no", "target": "Reference"}],
    )
    task_response["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    brain, model, *_ = setup_brain(tmp_path, record, [task_response])
    remember_prior_inputs(brain, set(brain.values) - {"hbl_no"})
    if rejected_next_plan:

        def reject(*args, **kwargs):
            raise ValueError("screen_match_action_surface_mismatch")

        monkeypatch.setattr("brain.graph.screen_decision.validate_or_replan", reject)
    brain._observe()

    payload = json.loads(model.prompts[0][len(CONTEXT_MAPPING_RULES) :])
    assert set(payload["notebook"]["verified_inputs"]) == set(brain.values) - {"hbl_no"}
    assert payload["notebook"]["remaining_inputs"] == ["hbl_no"]
    assert set(brain._observed_inputs()) == set(brain.values)
    current = brain._observed_inputs()["hbl_no"]["last_observation"]
    assert current["value"] == brain.values["hbl_no"]
    assert current["observation_id"] == str(brain.observation.obs_id)
    assert any(
        e.payload.get("check") == "model_task_evidence_recorded"
        and e.payload["fields"] == ["hbl_no"]
        for e in brain.log.read_all()
    )
    if rejected_next_plan:
        assert brain.visual_match_candidate is None
        assert brain.visual_proposal is not None
        assert brain.visual_plan_error is not None
        with pytest.raises(ValueError, match="screen_match_action_surface_mismatch"):
            brain._selection_call("unused", ScreenMatch)
        assert brain._observed_inputs()["hbl_no"]["last_observation"] == current
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
    assert ScreenMatch.model_validate(data).mapping is not None
    data["steps"][0]["target"] = "unresolvable executable target"
    with pytest.raises(KeyError, match="unresolvable executable target"):
        ScreenMatch.model_validate(data).mapping.local_payload({})


def test_group_click_role_policy_is_observed_without_rejection(tmp_path):
    from types import SimpleNamespace

    services = SimpleNamespace(
        settings=current_settings(tmp_path),
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


@pytest.mark.parametrize(
    ("save_result", "action", "expected_intent"),
    [
        ("ready_for_query", "click", "navigate"),
        ("confirmation_required", "click", "confirm_save"),
        ("ready_for_query", "key", None),
        ("rejected", "click", None),
    ],
)
def test_synthetic_save_acknowledgement_normalizes_only_completed_click(
    tmp_path, save_result, action, expected_intent
):
    """Synthetic reconstruction of 088-r3: same target, only execution intent changes."""
    from test_brain import element

    from brain.graph.save_review import SaveReviewResponse
    from sawon_runtime.storage import RunLog

    observed = wire()
    prefix = "e" + str(observed.obs_id.int) + "0"
    scene = SceneObservation(
        wire=observed,
        screen_kind="dialog",
        elements=[
            element(prefix + "1", "Text", name="Saved record 1"),
            element(prefix + "2", "Button", name="OK"),
        ],
    )
    response = ScreenMatch(
        screen_kind="dialog",
        controls=[],
        procedure_step="save-record",
        save_result=save_result,
        message_targets=[1],
        steps=[
            {
                "target": 2,
                "action": action,
                "intent": "confirm_save",
                "point": [288, 266],
                "keys": "enter" if action == "key" else None,
            }
        ],
    )
    original = response.model_dump_json()
    log = RunLog(tmp_path, case_id="synthetic-save", agent_id="test")
    if expected_intent is None:
        with pytest.raises(ValueError, match="next decision conflicts with save result"):
            compile_match(response, scene, SaveReviewResponse, log=log)
        assert not log.read_all()
    else:
        review = compile_match(response, scene, SaveReviewResponse, log=log)
        decision = review.next_decision.to_decision()
        assert review.result == save_result
        assert review.evidence_refs == [prefix + "1"]
        assert decision.intent == expected_intent
        assert decision.operation == "act"  # Acknowledgement does not finish the task.
        assert decision.action.type == "click" and decision.action.ref == prefix + "2"
        events = log.read_all()
        if expected_intent == "navigate":
            assert len(events) == 1
            assert events[0].payload["check"] == "save_acknowledgement_intent_normalized"
            assert events[0].payload["point"] == [288, 266]
            assert events[0].payload["target"] == 2
        else:
            assert not events
    assert response.model_dump_json() == original
