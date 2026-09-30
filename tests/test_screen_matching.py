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
    assert "Select one label" not in model.prompts[0]
    assert "secret-user" not in model.prompts[0] and "secret-pw" not in model.prompts[0]


def test_identical_capture_reuses_ocr_but_rematches_new_procedure_step(tmp_path, record):
    meaning = fixture()[-1]
    entry = response(
        meaning,
        procedure_step="open-entry",
        steps=[{"target": meaning.controls[0].id, "action": "click", "intent": "navigate"}],
    )
    entry["controls"][0]["role"] = "button"
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
    assert brain.decision.action.ref.startswith("e" + str(brain.observation.obs_id.int))
    relay.color = "black"
    brain._observe()
    assert ocr.calls == detector.calls == 2


def test_image_is_requested_only_after_explicit_ambiguity(tmp_path, record):
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
    assert images_seen == [False, True]


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
    brain, _, _, _, _, _ = setup_brain(tmp_path, record, [data])
    if bad == "wrong-procedure":
        with pytest.raises(ValueError, match="procedure_mismatch"):
            brain._observe()
    else:
        brain._observe()
        assert brain._select_node({}) != {"next": "act"}


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


@pytest.mark.parametrize("mode", ["present", "missing", "still_missing", "changed_value"])
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
    supplemental = {"controls": [] if mode == "still_missing" else [field]}
    brain, model, _, _, _, _ = setup_brain(tmp_path, record, [supplemental])
    brain.observation = before
    brain.query_descriptor = describe(
        next(e for e in before.elements if e.role == "edit"), before.elements
    )
    brain.query_window_handle = before.active_window.handle
    brain.query_submitted = brain.query_value_verified = True
    required = required_verification(brain)
    if mode == "changed_value":
        blocks[-1] = blocks[-1].model_copy(update={"text": "DIFFERENT-NOW"})
    screen = {"ocr": [b.model_dump() for b in blocks], "markers": [m.model_dump() for m in markers]}
    matched = ScreenMatch(
        screen_kind="query",
        procedure_step="check-duplicate",
        controls=[table, field] if mode == "present" else [table],
        operation="duplicate_clear",
        grid=table_id,
    )
    if mode == "still_missing":
        with pytest.raises(GuardStop, match="required_verification_missing"):
            retain_verification(brain, matched, screen, required)
        assert len(model.prompts) == 1
        return
    repaired = retain_verification(brain, matched, screen, required)
    assert len(model.prompts) == (0 if mode == "present" else 1)
    assert repaired.operation == matched.operation and repaired.steps == matched.steps
    brain.observation = bind_scene(wire(), blocks, ground_meanings(repaired, markers, blocks))
    if mode == "changed_value":
        assert not brain._current_query_matches()
    else:
        assert brain._current_query_matches()
        decision = compile_match(repaired, brain.observation, DecisionResponse).to_decision()
        assert brain._check_duplicate(decision) == {"next": "observe"}
        assert brain.phase == "enter" and brain.duplicate_clear
