"""Procedure-driven OCR matching shares one response with the existing executor."""

import json
from itertools import repeat
from pathlib import Path

import pytest
from PIL import Image
from test_brain import ScriptedModel
from test_brain import record as record
from test_visual_markers import fixture
from test_visual_scene import wire

from brain.cache import VisualMatchCache
from brain.graph.engine import Brain
from brain.graph.matching import (
    PLAN_RULES,
    RULES,
    TARGET_GROUNDING_RULES,
    TASK_PLAN_RULES,
    TASK_RULES,
    ScreenMatch,
    ScreenPlan,
    compile_match,
    compile_screen_action_plan,
)
from brain.graph.schemas import DecisionResponse
from brain.graph.screen_batch import LoginScreenPlan, ScreenFillPlan
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings
from brain.task_instruction import TaskInstruction
from brain.visual.scene import SceneObservation, SemanticDetection

PROCEDURE = Path(__file__).resolve().parents[1] / "examples/shipment-procedure.json"


def setup_brain(tmp_path, record, responses):
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
        if prompt.startswith((PLAN_RULES, TASK_PLAN_RULES)):
            return {key: value for key, value in item.items() if key in ScreenPlan.model_fields}
        if prompt.startswith(TARGET_GROUNDING_RULES):
            return {
                key: value for key, value in item.items() if key in SemanticDetection.model_fields
            }
        return item

    model = ScriptedModel(log, repeat(route))
    brain = Brain(
        record=record,
        goal="Register synthetic shipment",
        relay=relay,
        model=model,
        settings=BrainSettings(
            perception_mode="visual_only",
            screen_matching=True,
            visual_screen_fill=True,
            procedure_path=PROCEDURE,
        ),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=ocr,
    )
    brain.visual_perception.detector = detector
    return brain, model, relay, ocr, detector, meaning


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


def test_procedure_is_loaded_and_new_screen_match_is_one_combined_call(tmp_path, record):
    meaning = fixture()[-1]
    brain, model, _, _, _, _ = setup_brain(tmp_path, record, [response(meaning)])
    brain.credentials = {"credential.username": "secret-user", "credential.password": "secret-pw"}
    assert brain._plan_node({}) == {"next": "observe"}
    assert not model.prompts
    assert json.loads((tmp_path / "procedure.json").read_text())["steps"]
    brain._observe()
    assert brain._select_node({}) == {"next": "review"}
    assert brain.review_execution
    assert not any(event.kind == "act" for event in brain.log.read_all())
    assert brain._review_node({}) == {"next": "act"}
    assert not brain.review_execution
    assert any(
        event.payload.get("check") == "proposed_execution_review"
        and event.payload.get("stage") == "before_action"
        for event in brain.log.read_all()
    )
    assert len(model.prompts) == 1
    assert model.prompts[0].startswith(RULES)
    assert brain.decision.action.source == "hbl_no"
    assert brain.decision.action.ref.startswith("e" + str(brain.observation.obs_id.int))
    assert "secret-user" not in model.prompts[0] and "secret-pw" not in model.prompts[0]


def test_task_instruction_exposes_goal_and_capabilities_without_fixed_ui_route(tmp_path, record):
    meaning = fixture()[-1]
    task_response = response(
        meaning,
        procedure_step="로그인",
    )
    task_response["steps"][0]["source"] = record.fields.hbl_no.value
    brain, model, *_ = setup_brain(tmp_path, record, [task_response])
    brain.task_instruction = TaskInstruction.model_validate_json(
        (Path(__file__).resolve().parents[1] / "examples/shipment-task.json").read_text(
            encoding="utf-8"
        )
    )
    brain.procedure = None
    brain.phase = "task"
    brain.verified["hbl_no"] = "prior-screen-ref"

    assert brain._plan_node({}) == {"next": "observe"}
    brain._observe()

    assert len(model.prompts) == 1
    assert model.prompts[0].startswith(TASK_RULES)
    state = json.loads(model.prompts[0][len(TASK_RULES) :])
    assert state["task"]["objective"] == brain.task_instruction.objective
    assert state["notebook"]["inputs"] == brain.values
    assert state["notebook"]["verified_inputs"] == ["hbl_no"]
    assert state["notebook"]["remaining_inputs"] == sorted(brain.values)
    assert "record" not in state["notebook"]
    assert "remaining_fields" not in state["notebook"]
    persisted = json.loads((tmp_path / "task-notebook.json").read_text(encoding="utf-8"))
    assert persisted["inputs_path"] == "record.json"
    assert persisted["remaining_inputs"] == sorted(brain.values)
    assert "record_path" not in persisted
    assert "query_submitted" not in persisted
    assert [step["capability_id"] for step in state["capabilities"]] == ["task-action"]
    assert state["capabilities"][0]["view"] == "current.observed.screen"
    assert state["capabilities"][0]["input_scope"] == sorted(brain.values)
    assert "field_scope" not in state["capabilities"][0]
    assert "procedure" not in state
    assert brain.visual_proposal.procedure_step == "task-action"
    assert brain.visual_proposal.steps[0].intent == "fill"
    assert brain._select_node({}) == {"next": "review"}
    assert brain.action_plan.actions[0].decision.action.source == "hbl_no"
    assert not {
        "open-shipper-lookup",
        "lookup-partner",
        "open-additional-entry",
        "fill-additional-entry",
    } & {step["capability_id"] for step in state["capabilities"]}


@pytest.mark.parametrize("source", ["record.hbl_no", "inputs.hbl_no"])
def test_generic_task_binds_prompt_container_path_to_input_key(tmp_path, record, source):
    meaning = fixture()[-1]
    task_response = response(meaning, procedure_step="Enter the visible task input")
    task_response["steps"][0]["source"] = source
    task_response["evidence"] = [{"field": source, "target": meaning.controls[0].id}]
    brain, *_ = setup_brain(tmp_path, record, [task_response])
    brain.task_instruction = TaskInstruction.model_validate_json(
        (Path(__file__).resolve().parents[1] / "examples/shipment-task.json").read_text(
            encoding="utf-8"
        )
    )
    brain.procedure = None
    brain.phase = "task"

    brain._observe()

    assert brain.visual_proposal.procedure_step == "task-action"
    assert brain.visual_proposal.steps[0].intent == "fill"
    assert brain.visual_proposal.steps[0].source == "hbl_no"
    assert brain.visual_proposal.evidence[0].field == "hbl_no"


def test_generic_task_discards_prose_source_from_non_binding_click(tmp_path, record):
    meaning = fixture()[-1]
    task_response = response(meaning, procedure_step="Open the relevant work area")
    task_response["steps"][0].update(
        action="click",
        intent="navigate",
        source="House B/L work menu",
        point=[175, 30],
    )
    brain, *_ = setup_brain(tmp_path, record, [task_response])
    brain.task_instruction = TaskInstruction.model_validate_json(
        (Path(__file__).resolve().parents[1] / "examples/shipment-task.json").read_text(
            encoding="utf-8"
        )
    )
    brain.procedure = None
    brain.phase = "task"

    brain._observe()

    assert brain.visual_proposal.procedure_step == "task-action"
    assert brain.visual_proposal.steps[0].intent == "navigate"
    assert brain.visual_proposal.steps[0].source is None


def test_promoted_exact_visual_match_reuse_precedes_model_call(tmp_path, record):
    meaning = fixture()[-1]
    cache_path = tmp_path / "visual-match.json"

    first, first_model, *_ = setup_brain(tmp_path / "first", record, [response(meaning)])
    first.visual_match_cache = VisualMatchCache(cache_path, enabled=True)
    first._observe()
    assert len(first_model.prompts) == 1
    assert first.visual_match_candidate is not None
    first.visual_match_cache.stage(*first.visual_match_candidate)
    assert first.visual_match_cache.finish(success=True) == 1

    second, second_model, *_ = setup_brain(tmp_path / "second", record, [])
    second.visual_match_cache = VisualMatchCache(cache_path, enabled=True)
    second._observe()
    assert second_model.prompts == []
    assert second._select_node({}) == {"next": "review"}
    assert second_model.prompts == []
    hits = [
        event
        for event in second.log.read_all()
        if event.kind == "cache_hit" and event.payload.get("check") == "visual_match_cache"
    ]
    assert len(hits) == 1


def test_identical_capture_reuses_ocr_but_rematches_new_procedure_step(tmp_path, record):
    meaning = fixture()[-1]
    entry = response(
        meaning,
        procedure_step="open-entry",
        steps=[
            {
                "target": meaning.controls[0].id,
                "action": "click",
                "intent": "navigate",
                "point": [175, 30],
            }
        ],
    )
    entry["controls"][0]["role"] = "button"
    entry["evidence"] = [{"field": "hbl_no", "target": meaning.controls[0].id}]
    brain, model, relay, ocr, detector, _ = setup_brain(
        tmp_path, record, [response(meaning), entry, entry]
    )
    brain._observe()
    old_id = brain.observation.obs_id
    brain.phase, brain.duplicate_clear = "enter", True
    brain._observe()
    assert brain.observation.obs_id != old_id
    assert ocr.calls == detector.calls == len(model.prompts) == 1
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert len(model.prompts) == 2
    assert "open-entry" in model.prompts[-1]
    assert "enter-duplicate-key" not in model.prompts[-1]
    assert brain.decision.intent == "navigate"
    assert brain.saved_bindings == (
        str(brain.observation.obs_id),
        {"hbl_no": brain.decision.action.ref},
    )
    assert brain.decision.action.ref.startswith("e" + str(brain.observation.obs_id.int))
    action = brain._wire_action(brain.decision.action, brain.decision)
    assert action.root.target.image_point == (175, 30)
    assert action.root.target.evidence.startswith("image_planned:")
    relay.color = "black"
    brain._observe()
    assert ocr.calls == detector.calls == 2


def test_gemini_interprets_and_plans_in_one_image_call(tmp_path, record):
    meaning = fixture()[-1]
    brain, model, _, _, _, _ = setup_brain(
        tmp_path,
        record,
        [response(meaning)],
    )
    images_seen = []
    original = model._generate

    def generate(prompt, schema, *, images=None):
        images_seen.append(bool(images))
        return original(prompt, schema, images=images)

    model._generate = generate
    brain._observe()
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert images_seen == [True]
    assert model.prompts[0].startswith(RULES)


def test_local_grounding_retry_receives_only_the_relevant_correction(tmp_path, record):
    meaning = fixture()[-1]
    bad = response(meaning)
    bad["controls"][0]["id"] = 999
    corrected = response(meaning)
    brain, model, _, _, _, _ = setup_brain(tmp_path, record, [bad, corrected])
    brain._observe()
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert len(model.prompts) == 2
    assert '"error": "unknown_or_duplicate_marker_id"' in model.prompts[1]
    assert "rejected_response" not in model.prompts[1]


def test_identical_rematch_reuses_grounded_interpretation(tmp_path, record):
    meaning = fixture()[-1]
    full = response(meaning)
    brain, model, _, ocr, detector, _ = setup_brain(tmp_path, record, [full, full])
    brain._observe()
    brain._observe()
    assert len(model.prompts) == 1
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert len(model.prompts) == 2
    assert model.prompts[-1].startswith(PLAN_RULES)
    assert ocr.calls == detector.calls == 1
    assert any(
        event.payload.get("check") == "identical_form_reused" for event in brain.log.read_all()
    )


def test_identical_catalog_grounds_new_target_without_replacing_plan(tmp_path, record):
    meaning = fixture()[-1]
    empty = response(
        meaning,
        controls=[],
        procedure_step="enter-duplicate-key",
        steps=[],
        operation="stop",
    )
    open_entry = response(
        meaning,
        screen_kind="query",
        procedure_step="open-entry",
        controls=[
            {
                **response(meaning)["controls"][0],
                "role": "button",
            }
        ],
        steps=[
            {
                "target": meaning.controls[0].id,
                "action": "click",
                "intent": "navigate",
                "point": [175, 30],
            }
        ],
    )
    brain, model, _, ocr, detector, _ = setup_brain(
        tmp_path, record, [empty, open_entry, open_entry]
    )
    brain._observe()
    brain.phase = "enter"
    brain.duplicate_clear = True
    brain._observe()

    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert len(model.prompts) == 3
    assert model.prompts[1].startswith(PLAN_RULES)
    assert model.prompts[2].startswith(TARGET_GROUNDING_RULES)
    assert ocr.calls == detector.calls == 1
    assert any(
        event.payload.get("check") == "action_targets_grounded"
        and event.payload.get("plan_preserved") is True
        for event in brain.log.read_all()
    )
    assert any(
        event.payload.get("check") == "catalog_targets_merged"
        and event.payload.get("plan_preserved") is True
        for event in brain.log.read_all()
    )


def test_partner_lookup_fill_uses_single_decision_instead_of_editor_batch(tmp_path, record):
    meaning = fixture()[-1]
    partner = response(
        meaning,
        screen_kind="partner",
        procedure_step="lookup-partner",
        steps=[
            {
                "target": meaning.controls[0].id,
                "action": "fill",
                "source": "shipper",
                "intent": "fill",
            }
        ],
    )
    brain, _, _, _, _, _ = setup_brain(tmp_path, record, [partner])
    brain.phase = "enter"
    brain.duplicate_clear = True
    brain.verified = {
        field: f"ref-{field}"
        for field in {"hbl_no", "mbl_no", "packages.count", "packages.unit", "pol"}
    }

    brain._observe()

    assert brain.observation.screen_kind == "partner"
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert brain.action_plan is None
    assert brain.decision.intent == "fill"
    assert brain.decision.action.source == "shipper"


@pytest.mark.parametrize("bad", ["wrong-procedure", "wrong-target", "wrong-phase"])
def test_invalid_mapping_cannot_reach_action(tmp_path, record, bad):
    meaning = fixture()[-1]
    data = response(meaning)
    if bad == "wrong-procedure":
        data["procedure_step"] = "save-entry"
    elif bad == "wrong-target":
        data["steps"][0]["target"] = 999
    else:
        data["steps"][0].update(action="click", intent="save", source=None, point=[175, 30])
    responses = [data, data] if bad in {"wrong-procedure", "wrong-phase"} else [data]
    brain, model, _, _, _, _ = setup_brain(tmp_path, record, responses)
    if bad in {"wrong-procedure", "wrong-phase"}:
        expected = {
            "wrong-procedure": "screen_match_procedure_mismatch",
            "wrong-phase": "screen_match_action_outside_procedure",
        }[bad]
        with pytest.raises(ValueError, match=expected):
            brain._observe()
        assert len(model.prompts) == 2
        assert model.prompts[-1].startswith(PLAN_RULES)
        assert not any(e.kind == "act" for e in brain.log.read_all())
        return
    brain._observe()
    schema = brain._decision_policy().response_schema()
    assert compile_match(ScreenMatch.model_validate(response(meaning)), brain.observation, schema)
    if bad == "wrong-target":
        with pytest.raises(ValueError, match="^visual_proposal_unknown_marker$"):
            compile_match(ScreenMatch.model_validate(data), brain.observation, schema)
    assert brain._select_node({}) == {"next": "observe"}
    failures = [
        e.payload["reason"]
        for e in brain.log.read_all()
        if e.kind == "verify" and e.payload.get("ok") is False
    ]
    expected = {
        "wrong-procedure": "screen_match_procedure_mismatch",
        "wrong-target": "visual_proposal_unknown_marker",
    }[bad]
    assert failures == ["selection_error:" + expected]
    assert len(model.prompts) == 1
    assert brain.pending is None and brain.action_plan is None
    assert not any(e.kind == "act" for e in brain.log.read_all())


def test_combined_match_keeps_grounding_and_corrects_only_invalid_action(tmp_path, record):
    meaning = fixture()[-1]
    wrong = response(meaning, procedure_step="save-entry")
    corrected = response(meaning)
    brain, model, _, _, _, _ = setup_brain(tmp_path, record, [wrong, corrected])

    brain._observe()

    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert len(model.prompts) == 2
    assert model.prompts[0].startswith(RULES)
    assert model.prompts[1].startswith(PLAN_RULES)
    correction = json.loads(model.prompts[1][len(PLAN_RULES) :])["correction"]
    assert correction["error"] == "screen_match_procedure_mismatch"
    assert any(
        event.payload.get("check") == "combined_action_corrected" for event in brain.log.read_all()
    )


def test_mixed_partner_work_is_corrected_to_current_selection_batch(tmp_path, record):
    meaning = fixture()[-1]
    mixed = response(
        meaning,
        screen_kind="partner",
        procedure_step="lookup-partner",
        steps=[
            {
                "target": meaning.controls[0].id,
                "action": "fill",
                "source": "shipper",
                "intent": "fill",
            },
            {
                "target": meaning.controls[0].id,
                "action": "click",
                "intent": "search",
                "point": [175, 30],
            },
            {
                "target": meaning.controls[0].id,
                "action": "click",
                "intent": "select_option",
                "point": [175, 30],
            },
        ],
    )
    corrected = response(
        meaning,
        screen_kind="partner",
        procedure_step="lookup-partner",
        steps=[
            {
                "target": meaning.controls[0].id,
                "action": "click",
                "intent": "select_option",
                "point": [175, 30],
            },
            {
                "target": meaning.controls[0].id,
                "action": "click",
                "source": "shipper",
                "intent": "select_option",
                "point": [175, 30],
            },
        ],
    )
    brain, model, _, _, _, _ = setup_brain(tmp_path, record, [mixed, corrected])
    brain.phase, brain.duplicate_clear = "enter", True
    brain.verified.update(
        {
            field: "verified"
            for field in ("hbl_no", "mbl_no", "packages.count", "packages.unit", "pol")
        }
    )

    brain._observe()

    assert len(model.prompts) == 2
    correction = json.loads(model.prompts[1][len(PLAN_RULES) :])["correction"]
    assert correction["error"] == "screen_match_mixed_boundary_actions"
    assert brain.visual_proposal.steps == ScreenMatch.model_validate(corrected).steps


def test_action_correction_grounds_real_target_without_replanning(tmp_path, record):
    meaning = fixture()[-1]
    wrong = response(meaning, procedure_step="save-entry", controls=[])
    corrected = response(meaning, controls=[])
    grounded = response(meaning)
    brain, model, _, ocr, detector, _ = setup_brain(tmp_path, record, [wrong, corrected, grounded])

    brain._observe()

    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}
    assert brain.decision.intent == "query_input"
    assert len(model.prompts) == 3
    assert model.prompts[-1].startswith(TARGET_GROUNDING_RULES)
    assert ocr.calls == detector.calls == 1
    assert any(
        event.payload.get("check") == "action_targets_grounded"
        and event.payload.get("plan_preserved") is True
        for event in brain.log.read_all()
    )


def test_missing_action_semantics_preserves_original_single_click(tmp_path, record):
    meaning = fixture()[-1]
    plan = response(
        meaning,
        screen_kind="query",
        procedure_step="open-entry",
        controls=[],
        steps=[
            {
                "target": meaning.controls[0].id,
                "action": "click",
                "intent": "navigate",
                "point": [175, 30],
            }
        ],
    )
    target = response(
        meaning,
        screen_kind="query",
        procedure_step="open-entry",
        controls=[{**response(meaning)["controls"][0], "role": "button"}],
        steps=[],
        operation="stop",
    )
    brain, model, _, ocr, detector, _ = setup_brain(tmp_path, record, [plan, target])
    brain.phase = "enter"
    brain.duplicate_clear = True

    brain._observe()
    assert brain._select_node({}) == {"next": "review"}
    assert brain._review_node({}) == {"next": "act"}

    assert len(model.prompts) == 2
    assert model.prompts[1].startswith(TARGET_GROUNDING_RULES)
    assert brain.action_plan is None
    assert brain.decision.intent == "navigate"
    assert brain.decision.action.ref.endswith(str(meaning.controls[0].id))
    assert brain.visual_points.get(brain.observation, brain.decision).point == (175.0, 30.0)
    assert ocr.calls == detector.calls == 1


def test_login_and_record_arrays_compile_to_existing_batches():
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
    with pytest.raises(ValueError, match="mixed_field_batch"):
        compile_match(
            fields.model_copy(update={"steps": [*fields.steps, login.steps[-1]]}),
            scene,
            ScreenFillPlan,
        )
    stop = ScreenMatch(
        screen_kind="query", controls=[], procedure_step="check-duplicate", operation="stop"
    )
    assert compile_match(stop, scene, DecisionResponse).to_decision().operation == "stop"


@pytest.mark.parametrize(
    ("intents", "audit_intent"),
    [
        (("navigate", "navigate"), "navigate"),
        (("select_option", "navigate"), "select_then_navigate"),
    ],
)
def test_same_screen_navigation_clicks_compile_to_one_action_plan(intents, audit_intent):
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
    assert [item.decision.action.ref for item in plan.actions] == [prefix + "31", prefix + "44"]
    assert plan.audit == {
        "check": "same_screen_action_plan",
        "actions": 2,
        "intent": audit_intent,
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
def test_query_verification_omission_is_repaired_without_reusing_old_value(tmp_path, record, mode):
    from brain.graph.execution import GuardStop
    from brain.graph.matching import required_verification, retain_verification
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
    brain.query_descriptor = describe(
        next(e for e in before.elements if e.role == "edit"), before.elements
    )
    brain.query_window_handle = before.active_window.handle
    brain.query_submitted = brain.query_value_verified = True
    if mode.startswith(("renamed", "resized")):
        brain.query_input_anchor = (
            before,
            next(e.ref for e in before.elements if e.role == "edit"),
        )
    required = required_verification(brain)
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
        procedure_step="check-duplicate",
        controls=[table, field]
        if mode == "present" or mode.startswith(("renamed", "resized"))
        else [table],
        operation="duplicate_clear",
        grid=table_id,
        evidence=[{"field": "hbl_no", "target": field_id}],
    )
    if mode == "still_missing":
        with pytest.raises(GuardStop, match="required_verification_missing"):
            retain_verification(brain, matched, screen, required)
        assert len(model.prompts) == 1
        return
    repaired = retain_verification(brain, matched, screen, required)
    assert len(model.prompts) == (
        0 if mode == "present" or mode.startswith(("renamed", "resized")) else 1
    )
    assert repaired.operation == matched.operation and repaired.steps == matched.steps
    brain.observation = bind_scene(current_wire, blocks, ground_meanings(repaired, markers, blocks))
    if mode in {"changed_value", "renamed_wrong_value", "resized_wrong_value"}:
        assert not brain._current_query_matches()
    else:
        assert brain._current_query_matches()
        decision = compile_match(repaired, brain.observation, DecisionResponse).to_decision()
        assert not decision.evidence
        assert repaired.evidence  # Retained as observations, not final completion proof.
        assert brain._check_duplicate(decision) == {"next": "observe"}
        assert brain.phase == "enter" and brain.duplicate_clear


def test_model_only_review_delegates_required_verification_without_repair_call(tmp_path, record):
    from brain.graph.matching import retain_verification

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

    assert retain_verification(brain, matched, {"ocr": [], "markers": []}, required) == matched
    assert not model.prompts
    delegated = [
        event
        for event in brain.log.read_all()
        if event.payload.get("check") == "required_verification_delegated"
    ]
    assert delegated[-1].payload["reviewer"] == "screen_model"
    assert delegated[-1].payload["sources"] == ["hbl_no"]


@pytest.mark.parametrize("operation", ["act", "stop"])
def test_empty_action_is_completed_once_or_explicitly_stopped(tmp_path, record, operation):
    meaning = fixture()[-1]
    initial = response(meaning, steps=[])
    completion = {
        "operation": operation,
        "steps": response(meaning)["steps"] if operation == "act" else [],
    }
    brain, model, _, _, _, _ = setup_brain(tmp_path, record, [initial, completion])
    brain._observe()
    result = brain._select_node({})
    assert len(model.prompts) == 2
    if operation == "act":
        assert result == {"next": "review"}
        assert brain._review_node({}) == {"next": "act"}
        assert brain.decision.action.source == "hbl_no"
    else:
        assert result == {"next": "finish"}
        assert brain.result.reason == "model_requested_stop"


def test_missing_action_completion_cannot_repeat_empty_act():
    from pydantic import ValidationError

    from brain.graph.matching import ActionCompletion

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

    from brain.graph.matching import compile_selection_plan

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
    plan = compile_selection_plan(match, scene)
    assert [a.decision.action.ref for a in plan.actions] == [prefix + "20", prefix + "31"]
    assert all(a.decision.action.source is None for a in plan.actions)
    assert [a.effect_source for a in plan.actions] == [None, "consignee"]
    assert plan.verification == "single"
    mixed = match.model_copy(
        update={"steps": [match.steps[0], match.steps[1].model_copy(update={"intent": "navigate"})]}
    )
    assert compile_selection_plan(mixed, scene) is None


def test_selection_plan_requires_and_preserves_final_record_effect():
    from types import SimpleNamespace
    from uuid import uuid4

    from brain.graph.matching import compile_selection_plan

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
    with pytest.raises(ValueError, match="selection_effect_source_required"):
        compile_selection_plan(match, scene)
    sourced = match.model_copy(
        update={"steps": [match.steps[0].model_copy(update={"source": "shipper"})]}
    )
    plan = compile_selection_plan(sourced, scene)
    assert plan.actions[0].effect_source == "shipper"


def test_unique_procedure_field_binds_selection_effect_without_model_echo():
    from brain.graph.matching import ProcedureStep, bind_selection_effect

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
    step = ProcedureStep.model_validate(
        {
            "id": "lookup-partner",
            "phase": "enter",
            "screen": "partner",
            "checkpoint": "fill_fields",
            "field_scope": ["shipper"],
            "goal": "Select the matching partner",
            "completion": "The editor field matches",
            "actions": ["select_option"],
        }
    )

    bound = bind_selection_effect(match, step, {"shipper"})
    assert [item.source for item in bound.steps] == [None, "shipper"]

    ambiguous = step.model_copy(update={"field_scope": ["shipper", "consignee"]})
    unchanged = bind_selection_effect(match, ambiguous, {"shipper", "consignee"})
    assert all(item.source is None for item in unchanged.steps)


def test_field_bound_lookup_navigation_requires_current_input_evidence():
    from brain.graph.matching import ProcedureStep, require_deferred_field_evidence

    opening = ProcedureStep.model_validate(
        {
            "id": "open-partner",
            "phase": "enter",
            "screen": "editor",
            "field_scope": ["shipper"],
            "goal": "Open the partner lookup",
            "completion": "The lookup is visible",
            "actions": ["navigate"],
        }
    )
    selecting = ProcedureStep.model_validate(
        {
            "id": "select-partner",
            "phase": "enter",
            "screen": "partner",
            "field_scope": ["shipper"],
            "goal": "Select the partner",
            "completion": "The editor value matches",
            "actions": ["select_option"],
        }
    )
    match = ScreenMatch.model_validate(
        {
            "screen_kind": "editor",
            "controls": [
                {"id": 12, "role": "edit", "confidence": 1},
                {"id": 13, "role": "button", "confidence": 1},
            ],
            "procedure_step": "open-partner",
            "steps": [
                {
                    "target": 13,
                    "action": "click",
                    "intent": "navigate",
                    "point": [1051, 222],
                }
            ],
        }
    )
    steps = [opening, selecting]
    with pytest.raises(ValueError, match="field_bound_navigation_evidence_required"):
        require_deferred_field_evidence(match, opening, steps, {"shipper"})

    wrong = ScreenMatch.model_validate(
        {**match.model_dump(), "evidence": [{"field": "shipper", "target": 13}]}
    )
    with pytest.raises(ValueError, match="field_bound_navigation_evidence_not_input"):
        require_deferred_field_evidence(wrong, opening, steps, {"shipper"})

    valid = ScreenMatch.model_validate(
        {**match.model_dump(), "evidence": [{"field": "shipper", "target": 12}]}
    )
    require_deferred_field_evidence(valid, opening, steps, {"shipper"})

    not_yet_grounded = ScreenMatch.model_validate(
        {**match.model_dump(), "evidence": [{"field": "shipper", "target": 99}]}
    )
    require_deferred_field_evidence(not_yet_grounded, opening, steps, {"shipper"})


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
    assert brain._verify_node({}) == {"next": "review"}
    assert "shipper" in brain.verified
    assert "shipper" not in brain.deferred_field_checks
    assert any(
        event.payload.get("check") == "selected_field_value_matches"
        for event in brain.log.read_all()
    )


def test_save_matching_receives_same_action_contract_as_validator(tmp_path, record):
    from brain.graph.matching import RULES, match_screen
    from brain.graph.save_review import SAVE_RESULT_ACTIONS, SAVE_REVIEW_GUIDANCE

    brain, model, _, _, _, meaning = setup_brain(
        tmp_path,
        record,
        [
            response(
                fixture()[-1],
                screen_kind="dialog",
                procedure_step="inspect-save",
                steps=[],
                operation="stop",
                save_result="unknown",
            )
        ],
    )
    brain.phase = "save_result"
    match_screen(
        brain,
        {
            "ocr": [b.model_dump() for b in fixture()[0]],
            "markers": [m.model_dump() for m in fixture()[2]],
        },
        None,
    )
    state = json.loads(model.prompts[-1][len(RULES) :])
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
    import json

    from brain.graph.matching import RULES, match_screen
    from brain.graph.schemas import Choice, Decision

    brain, model, _, _, _, _ = setup_brain(
        tmp_path,
        record,
        [
            response(
                fixture()[-1],
                screen_kind="query",
                procedure_step="search-duplicate" if phase == "duplicate_query" else "search-final",
                steps=[],
                operation="stop",
            )
        ],
    )
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
    match_screen(
        brain,
        {
            "ocr": [b.model_dump() for b in fixture()[0]],
            "markers": [m.model_dump() for m in fixture()[2]],
        },
        None,
    )
    state = json.loads(model.prompts[-1][len(RULES) :])
    assert state["notebook"]["query_value_verified"] is False
    assert state["proposal_after_verification"]["checkpoint"] == "search"
    assert "search" in state["execution"]["allowed_intents"]
    assert "query_input" not in state["execution"]["allowed_intents"]
    assert not any(s["checkpoint"] == "query_input" for s in state["procedure"])
    assert any(s["checkpoint"] == "search" for s in state["procedure"])
    assert brain.query_value_verified is False


def test_delivered_final_search_exposes_only_evidence_step(tmp_path, record):
    from brain.graph.matching import RULES, match_screen
    from brain.graph.schemas import Choice, Decision

    brain, model, _, _, _, _ = setup_brain(
        tmp_path,
        record,
        [
            response(
                fixture()[-1],
                screen_kind="query",
                procedure_step="verify-final",
                steps=[],
                operation="stop",
            )
        ],
    )
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

    match_screen(
        brain,
        {
            "ocr": [block.model_dump() for block in fixture()[0]],
            "markers": [marker.model_dump() for marker in fixture()[2]],
        },
        None,
    )

    state = json.loads(model.prompts[-1][len(RULES) :])
    candidate_ids = {step["id"] for step in state["procedure"]}
    assert "verify-final" in candidate_ids
    assert not {"enter-final-key", "search-final"} & candidate_ids
    assert state["execution"]["checkpoint"] == "final_row_evidence"


@pytest.mark.parametrize("pending", [False, True])
def test_entry_prompt_contains_only_remaining_and_pending_record_fields(tmp_path, record, pending):
    from brain.graph.matching import RULES, match_screen
    from brain.graph.schemas import Choice, Decision

    procedure_step = "save-entry" if pending else "fill-additional-entry"
    brain, model, _, _, _, _ = setup_brain(
        tmp_path,
        record,
        [
            response(
                fixture()[-1],
                screen_kind="editor",
                procedure_step=procedure_step,
                steps=[],
                operation="stop",
            )
        ],
    )
    brain.phase = "enter"
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

    match_screen(
        brain,
        {
            "ocr": [block.model_dump() for block in fixture()[0]],
            "markers": [marker.model_dump() for marker in fixture()[2]],
        },
        None,
    )
    state = json.loads(model.prompts[-1][len(RULES) :])
    notebook = state["notebook"]
    assert set(notebook["record"]) == {"consignee"}
    assert notebook["remaining_fields"] == ([] if pending else ["consignee"])
    assert notebook["pending_verification_fields"] == (["consignee"] if pending else [])
    checkpoints = {step["checkpoint"] for step in state["procedure"]}
    assert ("save" if pending else "fill_fields") in checkpoints
    assert checkpoints <= {"any", "save" if pending else "fill_fields"}
    if not pending:
        field_steps = {step["id"]: step for step in state["procedure"] if step["field_scope"]}
        assert set(field_steps) == {
            "open-additional-entry",
            "fill-additional-entry",
        }
        assert field_steps["open-additional-entry"]["completion"] == (
            "추가정보 직접 입력 필드가 보임"
        )
        assert field_steps["fill-additional-entry"]["goal"].startswith("추가정보 화면에서")
        assert "user-taught work memo" in RULES


def test_pending_tab_navigation_offers_its_successor_not_the_same_step(tmp_path, record):
    from brain.graph.matching import RULES, match_screen
    from brain.graph.schemas import Choice, Decision

    brain, model, _, _, _, _ = setup_brain(
        tmp_path,
        record,
        [
            response(
                fixture()[-1],
                screen_kind="editor",
                procedure_step="fill-additional-entry",
                steps=[],
                operation="stop",
            )
        ],
    )
    brain.phase = "enter"
    brain.duplicate_clear = True
    brain.verified = {
        field: f"prior-{field}"
        for field in brain.values
        if field not in {"consignee", "description", "gross_weight_kg", "container_no", "pod"}
    }
    brain.decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="select_tab",
        subgoal="open-additional-entry",
        reason="open the additional form",
        action=Choice(type="click", ref="e1"),
    )
    brain.pending = {"decision": brain.decision}

    match_screen(
        brain,
        {
            "ocr": [block.model_dump() for block in fixture()[0]],
            "markers": [marker.model_dump() for marker in fixture()[2]],
        },
        None,
    )

    state = json.loads(model.prompts[-1][len(RULES) :])
    candidate_ids = {step["id"] for step in state["procedure"]}
    assert "open-additional-entry" not in candidate_ids
    assert "fill-additional-entry" in candidate_ids


def test_multi_action_verification_step_remains_eligible_after_navigation(tmp_path, record):
    from brain.graph.matching import exclude_current_navigation_step
    from brain.graph.schemas import Choice, Decision

    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "final_query"
    brain.decision = Decision(
        screen_kind="query",
        operation="act",
        intent="navigate",
        subgoal="verify-final",
        reason="open the linked detail",
        action=Choice(type="click", ref="e1"),
    )
    brain.pending = {"decision": brain.decision}
    candidates = [step for step in brain.procedure.steps if step.id == "verify-final"]

    assert exclude_current_navigation_step(brain, candidates) == candidates


def test_match_plan_rejects_an_action_outside_the_procedure_step(tmp_path, record):
    from brain.graph.matching import match_screen

    meaning = fixture()[-1]
    bad = response(
        meaning,
        screen_kind="editor",
        procedure_step="open-final-query",
        steps=[
            {
                "target": meaning.controls[0].id,
                "action": "click",
                "source": None,
                "intent": "select_tab",
                "point": [25.0, 25.0],
            }
        ],
    )
    brain, *_ = setup_brain(tmp_path, record, [bad, bad])
    brain.phase = "final_query"
    brain.duplicate_clear = brain.save_issued = True
    brain.query_value_verified = brain.query_submitted = True

    with pytest.raises(ValueError, match="action_outside_procedure"):
        match_screen(
            brain,
            {
                "ocr": [block.model_dump() for block in fixture()[0]],
                "markers": [marker.model_dump() for marker in fixture()[2]],
            },
            None,
        )


def test_pending_fields_are_verification_context_not_repeat_action_candidates(tmp_path, record):
    from brain.graph.matching import RULES, match_screen
    from brain.graph.schemas import Choice, Decision

    meaning = fixture()[-1]
    brain, model, _, _, _, _ = setup_brain(
        tmp_path,
        record,
        [
            response(
                meaning,
                screen_kind="editor",
                procedure_step="open-shipper-lookup",
                steps=[],
                operation="stop",
            )
        ],
    )
    brain.phase = "enter"
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

    match_screen(
        brain,
        {
            "ocr": [block.model_dump() for block in fixture()[0]],
            "markers": [marker.model_dump() for marker in fixture()[2]],
        },
        None,
    )

    state = json.loads(model.prompts[-1][len(RULES) :])
    candidate_ids = {step["id"] for step in state["procedure"]}
    assert "open-shipper-lookup" in candidate_ids
    assert "fill-entry" not in candidate_ids
    assert state["notebook"]["pending_verification_fields"] == ["hbl_no"]
    assert set(state["notebook"]["record"]) == {"hbl_no", "shipper"}


def test_entry_step_rejects_a_field_outside_user_procedure_scope(tmp_path, record):
    from brain.graph.matching import match_screen

    meaning = fixture()[-1]
    bad = response(
        meaning,
        screen_kind="editor",
        procedure_step="fill-entry",
        steps=[
            {
                "target": meaning.controls[0].id,
                "action": "fill",
                "source": "shipper",
                "intent": "fill",
            }
        ],
    )
    brain, *_ = setup_brain(tmp_path, record, [bad, bad])
    brain.phase = "enter"
    brain.duplicate_clear = True
    with pytest.raises(ValueError, match="field_outside_procedure_scope"):
        match_screen(
            brain,
            {
                "ocr": [block.model_dump() for block in fixture()[0]],
                "markers": [marker.model_dump() for marker in fixture()[2]],
            },
            None,
        )


def test_final_row_matching_receives_visibility_and_action_contract(tmp_path, record):
    import json

    from brain.graph.matching import RULES, match_screen

    brain, model, _, _, _, _ = setup_brain(
        tmp_path,
        record,
        [
            response(
                fixture()[-1],
                screen_kind="query",
                procedure_step="verify-final",
                steps=[],
                operation="stop",
            )
        ],
    )
    brain.phase = "final_query"
    brain.query_value_verified = brain.query_submitted = True
    match_screen(
        brain,
        {
            "ocr": [b.model_dump() for b in fixture()[0]],
            "markers": [m.model_dump() for m in fixture()[2]],
        },
        None,
    )
    state = json.loads(model.prompts[-1][len(RULES) :])
    assert "search" not in state["execution"]["allowed_intents"]
    assert "window expansion" in state["execution"]["guidance"]
    assert "reuse earlier refs" in state["execution"]["guidance"]


@pytest.mark.parametrize(
    "dependency", ["none", "action", "evidence", "parent", "verification", "other_verification"]
)
def test_only_unused_invalid_roles_are_omitted(tmp_path, record, dependency):
    from brain.graph.matching import sanitize_unused_controls
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
        brain,
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
    from brain.graph.matching import sanitize_unused_controls
    from brain.visual.markers import Marker, ground_meanings
    from brain.visual.scene import SemanticDetection, bind_scene
    from pipeline.schemas import OcrBlock

    brain, *_ = setup_brain(tmp_path, record, [])
    blocks = [OcrBlock(id=0, text="조회 결과 0건", bbox=(10, 110, 70, 15), confidence=1)]
    markers = [
        Marker(id=16, bbox=(0, 0, 100, 100), source="pixel_rectangle", confidence=1),
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
        brain,
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
def test_only_missing_evidence_for_already_verified_fields_is_omitted(tmp_path, record, verified):
    from brain.graph.matching import sanitize_unused_controls
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
        brain,
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
    assert bool(response.evidence) is not verified
    meanings = SemanticDetection.model_validate(
        {
            key: value
            for key, value in response.model_dump().items()
            if key in SemanticDetection.model_fields
        }
    )
    scene = bind_scene(wire(), [], ground_meanings(meanings, markers, []))
    if verified:
        decision = compile_match(response, scene, DecisionResponse).to_decision()
        assert decision.intent == "save"
    else:
        with pytest.raises(ValueError, match="visual_proposal_unknown_marker"):
            compile_match(response, scene, DecisionResponse)


@pytest.mark.parametrize(
    ("phase", "submitted", "required"),
    [("final_query", True, False), ("final_query", False, True), ("duplicate_query", True, True)],
)
def test_query_input_requirement_ends_only_after_final_search(
    tmp_path, record, phase, submitted, required
):
    from brain.graph.matching import required_verification

    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = phase
    brain.query_submitted = submitted
    brain.query_value_verified = True
    brain.query_descriptor = {"role": "edit", "name": "Identifier"}
    assert bool(required_verification(brain)) is required
