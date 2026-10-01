"""Procedure-driven OCR matching shares one response with the existing executor."""

import json
from pathlib import Path

import pytest
from PIL import Image
from test_brain import ScriptedModel
from test_brain import record as record
from test_visual_markers import fixture
from test_visual_scene import wire

from brain.graph.engine import Brain
from brain.graph.matching import ScreenMatch, compile_match
from brain.graph.schemas import DecisionResponse
from brain.graph.screen_batch import LoginScreenPlan, ScreenFillPlan
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings
from brain.visual.scene import SceneObservation

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
    model = ScriptedModel(log, responses)
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


def test_procedure_is_loaded_without_model_and_form_match_is_single_call(tmp_path, record):
    meaning = fixture()[-1]
    brain, model, _, _, _, _ = setup_brain(tmp_path, record, [response(meaning)])
    brain.credentials = {"credential.username": "secret-user", "credential.password": "secret-pw"}
    assert brain._plan_node({}) == {"next": "observe"}
    assert not model.prompts
    assert json.loads((tmp_path / "procedure.json").read_text())["steps"]
    brain._observe()
    assert brain._select_node({}) == {"next": "act"}
    assert len(model.prompts) == 1
    assert brain.decision.action.source == "hbl_no"
    assert brain.decision.action.ref.startswith("e" + str(brain.observation.obs_id.int))
    assert "secret-user" not in model.prompts[0] and "secret-pw" not in model.prompts[0]


def test_identical_capture_reuses_ocr_but_rematches_new_procedure_step(tmp_path, record):
    meaning = fixture()[-1]
    entry = response(
        meaning,
        procedure_step="open-entry",
        steps=[{"target": meaning.controls[0].id, "action": "click", "intent": "navigate"}],
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
    assert brain._select_node({}) == {"next": "act"}
    assert len(model.prompts) == 2
    assert "open-entry" in model.prompts[-1]
    assert "enter-duplicate-key" not in model.prompts[-1]
    assert brain.decision.intent == "navigate"
    assert brain.saved_bindings == (
        str(brain.observation.obs_id),
        {"hbl_no": brain.decision.action.ref},
    )
    assert brain.decision.action.ref.startswith("e" + str(brain.observation.obs_id.int))
    relay.color = "black"
    brain._observe()
    assert ocr.calls == detector.calls == 2


def test_gemini_matches_image_and_ocr_together_and_can_retry_ambiguity(tmp_path, record):
    meaning = fixture()[-1]
    brain, model, _, _, _, _ = setup_brain(
        tmp_path,
        record,
        [response(meaning, needs_image=True, steps=[], operation="stop"), response(meaning)],
    )
    images_seen = []
    original = model._generate

    def generate(prompt, schema, *, images=None):
        images_seen.append(bool(images))
        return original(prompt, schema, images=images)

    model._generate = generate
    brain._observe()
    assert brain._select_node({}) == {"next": "act"}
    assert images_seen == [True, True]


def test_local_grounding_retry_receives_only_the_relevant_correction(tmp_path, record):
    meaning = fixture()[-1]
    bad = response(meaning)
    bad["controls"][0]["id"] = 999
    brain, model, _, _, _, _ = setup_brain(tmp_path, record, [bad, response(meaning)])
    brain._observe()
    assert brain._select_node({}) == {"next": "act"}
    assert len(model.prompts) == 2
    assert '"error": "unknown_or_duplicate_marker_id"' in model.prompts[1]
    assert "rejected_response" not in model.prompts[1]


@pytest.mark.parametrize("missing_control", [False, True])
def test_rematch_terminal_error_does_not_restart_local_repair(tmp_path, record, missing_control):
    meaning = fixture()[-1]
    bad = response(meaning)
    if missing_control:
        bad["needs_refinement"] = True
    else:
        bad["controls"][0]["id"] = 999
    brain, model, _, ocr, detector, _ = setup_brain(tmp_path, record, [response(meaning), bad, bad])
    brain._observe()
    brain._observe()  # Identical pixels keep OCR/detector; selection must rematch.
    assert len(model.prompts) == 1
    assert brain._select_node({}) == {"next": "finish"}
    reason = (
        "visual_required_control_missing" if missing_control else "unknown_or_duplicate_marker_id"
    )
    assert brain.result.reason == "visual_grounding:" + reason
    assert brain.result.status == "failed"
    assert len(model.prompts) == (2 if missing_control else 3)
    assert ocr.calls == detector.calls == 1
    assert not any(event.kind == "act" for event in brain.log.read_all())


@pytest.mark.parametrize("bad", ["wrong-procedure", "wrong-target", "wrong-phase"])
def test_invalid_mapping_cannot_reach_action(tmp_path, record, bad):
    meaning = fixture()[-1]
    data = response(meaning)
    if bad == "wrong-procedure":
        data["procedure_step"] = "save-entry"
    elif bad == "wrong-target":
        data["steps"][0]["target"] = 999
    else:
        data["steps"][0].update(action="click", intent="save", source=None)
    brain, model, _, _, _, _ = setup_brain(tmp_path, record, [data])
    if bad == "wrong-procedure":
        with pytest.raises(ValueError, match="procedure_mismatch"):
            brain._observe()
    else:
        brain._observe()
        schema = brain._decision_policy().response_schema()
        assert compile_match(
            ScreenMatch.model_validate(response(meaning)), brain.observation, schema
        )
        if bad == "wrong-target":
            with pytest.raises(ValueError, match="^visual_proposal_unknown_marker$"):
                compile_match(ScreenMatch.model_validate(data), brain.observation, schema)
        else:
            from pydantic import ValidationError

            with pytest.raises(ValidationError):
                compile_match(ScreenMatch.model_validate(data), brain.observation, schema)
        assert brain._select_node({}) == {"next": "observe"}
        reason = "ValueError" if bad == "wrong-target" else "ValidationError"
        failures = [
            e.payload["reason"]
            for e in brain.log.read_all()
            if e.kind == "verify" and e.payload.get("ok") is False
        ]
        assert failures == ["selection_error:" + reason]
    assert len(model.prompts) == 1
    assert brain.pending is None and brain.action_plan is None
    assert not any(e.kind == "act" for e in brain.log.read_all())


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
            {"target": 3, "action": "click", "intent": "login"},
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
        steps=[{"target": 2, "action": "click", "intent": "confirm_save"}],
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
        assert result == {"next": "act"}
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
                {"target": 20, "action": "click", "source": "consignee", "intent": "select_option"},
                {"target": 31, "action": "click", "intent": "select_option"},
            ],
        }
    )
    plan = compile_selection_plan(match, scene)
    assert [a.decision.action.ref for a in plan.actions] == [prefix + "20", prefix + "31"]
    assert all(a.decision.action.source is None for a in plan.actions)
    assert plan.verification == "single"
    mixed = match.model_copy(
        update={"steps": [match.steps[0], match.steps[1].model_copy(update={"intent": "navigate"})]}
    )
    assert compile_selection_plan(mixed, scene) is None


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
    from brain.graph.matching import omit_unused_invalid_roles
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
        "steps": [{"target": 55, "action": "click", "intent": "navigate"}],
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
    cleaned = omit_unused_invalid_roles(
        brain,
        response,
        {"markers": [m.model_dump() for m in markers], "ocr": [b.model_dump() for b in blocks]},
        required,
    )
    meanings = SemanticDetection.model_validate(
        {k: v for k, v in cleaned.model_dump().items() if k in SemanticDetection.model_fields}
    )
    if dependency in {"none", "other_verification"}:
        scene = bind_scene(wire(), blocks, ground_meanings(meanings, markers, blocks))
        decision = compile_match(cleaned, scene, DecisionResponse).decision
        assert decision.intent == "navigate"
        assert next(e for e in scene.elements if e.ref == decision.action.ref).name == "Details"
        assert not any(e.role == "edit" for e in scene.elements)
    else:
        with pytest.raises(ValueError, match="ocr_text_is_not_control_extent"):
            ground_meanings(meanings, markers, blocks)


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
