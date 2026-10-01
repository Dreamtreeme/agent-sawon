"""Choice batches retain the existing visual grounding, executor and billing boundary."""

import io
import json
from decimal import Decimal
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest
from pydantic import ValidationError
from test_brain import ScriptedModel
from test_brain import record as record
from test_screen_matching import response, setup_brain

from brain.graph.jev_matching import build_request, compile_answers
from brain.graph.matching import compile_match
from brain.graph.screen_batch import LoginScreenPlan, ScreenFillPlan
from brain.runtime.storage import RunLog
from brain.visual.markers import Marker, ground_meanings
from models.base import UsageUnavailableError
from models.factory import create_model_client
from models.jev import ChoiceAnswer, ChoiceAnswers, JevClient
from models.pricing import PriceRate
from pipeline.schemas import OcrBlock


def state():
    return {
        "procedure": [
            {
                "id": "fill",
                "screen": "editor",
                "goal": "Fill remaining fields",
                "completion": "Current values match",
            }
        ],
        "notebook": {
            "phase": "enter",
            "record": {"hbl_no": "SYNTH-1", "mbl_no": "SYNTH-2"},
            "available_credentials": [],
            "verified_fields": [],
        },
        "screen": {
            "ocr": [{"id": 20, "text": "Reference", "bbox": [0, 0, 40, 10], "confidence": 1}],
            "markers": [
                {"id": 1, "source": "omniparser", "bbox": [50, 0, 40, 15], "contained_ocr_ids": []},
                {
                    "id": 2,
                    "source": "omniparser",
                    "bbox": [50, 25, 40, 15],
                    "contained_ocr_ids": [],
                },
                {
                    "id": 3,
                    "source": "ocr",
                    "bbox": [0, 0, 40, 10],
                    "text_ids": [20],
                    "contained_ocr_ids": [20],
                },
            ],
        },
    }


def answers(request, **choices):
    selected = {}
    for key, question in request.questions.items():
        default = "none" if "none" in question.criteria else next(iter(question.criteria))
        choice = choices.get(key, default)
        selected[key] = {
            "type": "choice",
            "choice": choice,
            "confidence": 1,
            "probabilities": {c: float(c == choice) for c in question.criteria},
        }
    return ChoiceAnswers.model_validate({"answers": selected})


def test_parallel_field_mapping_compiles_shared_batch_and_grounding():
    request = build_request(state())
    assert (
        request.questions["role:1"].instructions["marker"]["nearby_ocr"][0]["text"] == "Reference"
    )
    assert "Reference" in request.questions["input:hbl_no"].criteria["1"]
    result = compile_answers(
        request,
        answers(
            request,
            **{
                "screen": "editor",
                "operation": "act",
                "input:hbl_no": "1",
                "input:mbl_no": "2",
                "role:1": "edit",
                "role:2": "edit",
                "role:3": "text",
                "label:1": "20",
            },
        ),
    )
    assert [(s.source, s.target) for s in result.steps] == [("hbl_no", 1), ("mbl_no", 2)]
    from types import SimpleNamespace
    from uuid import uuid4

    obs_id = uuid4()
    observation = SimpleNamespace(
        obs_id=obs_id, elements=[SimpleNamespace(ref=f"e{obs_id.int}0{i}") for i in (1, 2, 3)]
    )
    plan = compile_match(result, observation, ScreenFillPlan)
    assert len(plan.fields) == 2
    markers = [
        Marker.model_validate(
            {k: v for k, v in m.items() if k != "contained_ocr_ids"} | {"confidence": 1}
        )
        for m in state()["screen"]["markers"]
    ]
    grounded = ground_meanings(result, markers, [OcrBlock(**state()["screen"]["ocr"][0])])
    assert grounded.controls[0].bbox == [50, 0, 40, 15]
    assert "3" not in request.questions["input:hbl_no"].criteria  # OCR isn't an input


def test_login_compiles_credentials_and_click_in_one_batch():
    data = state()
    data["procedure"][0]["screen"] = "login"
    data["notebook"]["available_credentials"] = ["credential.username", "credential.password"]
    request = build_request(data)
    result = compile_answers(
        request,
        answers(
            request,
            **{
                "screen": "login",
                "operation": "act",
                "input:credential.username": "1",
                "input:credential.password": "2",
                "command": "login",
                "command_target:login": "3",
                "role:1": "edit",
                "role:2": "edit",
                "role:3": "button",
            },
        ),
    )
    assert [s.intent for s in result.steps] == ["credential", "credential", "login"]
    from types import SimpleNamespace
    from uuid import uuid4

    obs_id = uuid4()
    observation = SimpleNamespace(
        obs_id=obs_id, elements=[SimpleNamespace(ref=f"e{obs_id.int}0{i}") for i in (1, 2, 3)]
    )
    assert len(compile_match(result, observation, LoginScreenPlan).fields) == 2


@pytest.mark.parametrize("navigate", [False, True])
def test_only_selected_branch_is_executed_and_conflicting_fills_request_image(navigate):
    request = build_request(state())
    result = compile_answers(
        request,
        answers(
            request,
            **{
                "screen": "editor",
                "operation": "act",
                "input:hbl_no": "1",
                "input:mbl_no": "1",
                "command": "navigate" if navigate else "none",
                "command_target:navigate": "3",
            },
        ),
    )
    if navigate:
        assert not result.needs_image
        assert len(result.steps) == 1 and result.steps[0].intent == "navigate"
    else:
        assert result.needs_image and result.steps == []


def test_exact_question_and_option_sets_required():
    request = build_request(state())
    reply = answers(request)
    del reply.answers["screen"]
    with pytest.raises(ValueError, match="question_set"):
        compile_answers(request, reply)
    reply = answers(request)
    reply.answers["command_target:login"].probabilities["999"] = 0
    with pytest.raises(ValueError, match="option_set"):
        compile_answers(request, reply)


def test_provider_rounded_probabilities_are_preserved():
    answer = ChoiceAnswer(
        type="choice",
        choice="button",
        confidence=0.9,
        probabilities={"button": 0.94, "text": 0.03, "disabled:button": 0.02},
    )
    assert answer.probabilities["button"] == 0.94


def test_ocr_text_tab_can_be_selected_without_detector_box():
    request = build_request(state())
    result = compile_answers(
        request,
        answers(
            request,
            **{
                "screen": "editor",
                "operation": "act",
                "command": "select_tab",
                "command_target:select_tab": "3",
                "role:3": "tabitem",
            },
        ),
    )
    assert result.steps[0].target == 3
    assert result.controls[2].text_ids == [20]


@pytest.mark.parametrize(
    "needs_image,low_confidence", [(False, False), (True, False), (False, True)]
)
def test_actual_brain_routes_only_ambiguity_to_image_model(
    tmp_path, record, needs_image, low_confidence
):
    from test_visual_markers import fixture

    brain, gemini, _, _, _, meaning = setup_brain(tmp_path, record, [response(fixture()[-1])])

    class JevFake(ScriptedModel):
        def _generate(self, prompt, schema, *, images=None):
            from models.base import ProviderReply, TokenUsage
            from models.jev import ChoiceRequest

            assert not images
            request = ChoiceRequest.model_validate_json(prompt)
            self.request = request
            target = str(meaning.controls[0].id)
            reply = answers(
                request,
                **{
                    "screen": "query",
                    "procedure": "enter-duplicate-key",
                    "operation": "image" if needs_image else "act",
                    "input:hbl_no": "none" if needs_image else target,
                    "role:" + target: "edit",
                    "label:" + target: "8",
                },
            )
            if low_confidence:
                reply.answers["role:" + target].confidence = 0.4
            return ProviderReply(
                reply.model_dump_json(), TokenUsage(input_tokens=0, response_tokens=0)
            )

    brain.matching_model = JevFake(brain.log, [])
    seen = []
    generate = gemini._generate

    def image_call(prompt, schema, *, images=None):
        seen.append(bool(images))
        return generate(prompt, schema, images=images)

    gemini._generate = image_call
    brain.credentials = {"credential.username": "SECRET_USER", "credential.password": "SECRET_PW"}
    brain._observe()
    assert brain._select_node({}) == {"next": "act"}
    assert seen == ([True] if needs_image or low_confidence else [])
    assert sum(e.kind == "model_call" for e in brain.log.read_all()) == 1 + bool(
        needs_image or low_confidence
    )
    assert "SECRET_" not in brain.matching_model.request.model_dump_json()
    assert len(list((tmp_path / "jev").glob("*/answers.json"))) == 1


def test_disabled_target_is_preserved_for_existing_execution_guard():
    request = build_request(state())
    result = compile_answers(
        request,
        answers(
            request,
            **{
                "screen": "editor",
                "operation": "stop",
                "role:1": "disabled:edit",
            },
        ),
    )
    assert result.controls[0].role == "edit"
    assert not result.controls[0].enabled


def test_command_target_questions_are_explicit_and_missing_target_routes_to_image():
    request = build_request(state())
    assert "login" in request.questions["command_target:login"].instructions
    assert "command_target" not in request.questions
    result = compile_answers(
        request,
        answers(
            request,
            **{
                "screen": "login",
                "operation": "act",
                "command": "login",
                "command_target:login": "none",
            },
        ),
    )
    assert result.needs_image and result.steps == []


def test_pending_query_does_not_request_a_second_fill():
    data = state()
    data["notebook"]["pending_actions"] = [{"intent": "query_input", "source": "hbl_no"}]
    request = build_request(data)
    instruction = request.questions["input:hbl_no"].instructions
    assert "already delivered" in instruction
    assert "allowing the next search command" in instruction
    assert "select hbl_no even if it already matches" not in instruction


def client(tmp_path):
    log = RunLog(tmp_path, case_id="jev-test", agent_id="test")
    model = JevClient(
        api_key="test-secret",
        model="jev-1.13.0",
        run_log=log,
        price=PriceRate(input_usd_per_million="0.042", output_usd_per_million="0"),
    )
    return model, log


def test_http_shape_accounting_and_no_hidden_retry(tmp_path, monkeypatch):
    model, log = client(tmp_path)
    request = build_request(state())
    data = {
        **answers(request).model_dump(),
        "model": "jev-1.13.0",
        "usage": {"input_tokens": 1000, "output_tokens": 200},
    }
    opener = Mock(return_value=io.BytesIO(json.dumps(data).encode()))
    monkeypatch.setattr("models.jev.urlopen", opener)
    result = model.complete_json(request.model_dump_json(), ChoiceAnswers)
    assert result.usd == Decimal("0.000042")
    body = json.loads(opener.call_args.args[0].data)
    assert set(body) == {"state", "questions", "model"}
    assert len(log.read_all()) == opener.call_count == 1
    assert "test-secret" not in log.read_all()[0].model_dump_json()


@pytest.mark.parametrize("bad", ["missing_usage", "bad_answers", "http_error"])
def test_failure_keeps_accounting_and_does_not_retry(tmp_path, monkeypatch, bad):
    model, log = client(tmp_path)
    data = {"answers": {}, "usage": {"input_tokens": 100, "output_tokens": 10}}
    if bad == "missing_usage":
        data.pop("usage")
    elif bad == "bad_answers":
        data["answers"] = None
    opener = Mock(return_value=io.BytesIO(json.dumps(data).encode()))
    if bad == "http_error":
        opener.side_effect = HTTPError("https://api.typesafe.ai", 429, "private", {}, None)
    monkeypatch.setattr("models.jev.urlopen", opener)
    with pytest.raises((UsageUnavailableError, ValidationError, HTTPError)):
        model.complete_json(build_request(state()).model_dump_json(), ChoiceAnswers)
    event = log.read_all()[0]
    assert opener.call_count == 1
    assert (event.cost.usd is not None) == (bad == "bad_answers")
    assert event.payload["status"] == "error"


@pytest.mark.parametrize("key_name", ["TYPESAFE_API_KEY", "JEV_API_KEY"])
def test_jev_factory_uses_explicit_key_and_profile(tmp_path, key_name):
    from pathlib import Path

    model, log = client(tmp_path / "logs")
    env = tmp_path / ".env"
    env.write_text(f"{key_name}=fake-key\n", encoding="utf-8")
    config = Path(__file__).resolve().parents[1] / "config/models.jev.toml"
    assert isinstance(create_model_client(config, run_log=log, env_path=env), JevClient)
