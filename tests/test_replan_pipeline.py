"""Synthetic boundary responses exercise the common correction pipeline."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_brain import make_record
from test_screen_decision import matching_contract, named_response, setup_brain

from brain.graph import screen_decision as decision
from brain.graph.screen_mapping import CONTEXT_MAPPING_RULES


@pytest.mark.parametrize(
    ("error", "artifact", "check", "instruction"),
    [
        (
            "screen_match_action_surface_mismatch",
            "action-surface-correction",
            "action_surface_corrected",
            "semantic field name",
        ),
        (
            "screen_match_action_target_uncertain",
            "uncertain-target-correction",
            "uncertain_action_target_corrected",
            "local visual confidence",
        ),
    ],
)
def test_synthetic_replan_keeps_corrected_target_and_rejects_invalid_correction(
    monkeypatch, error, artifact, check, instruction
):
    original = decision.ScreenMatch(
        screen_kind="editor", procedure_step="task-action", controls=[], steps=[]
    )
    corrected = original.model_copy(
        update={
            "steps": [
                decision.MatchAction(target=4, action="click", intent="navigate", point=[10, 20])
            ]
        }
    )
    events, requests, artifacts, stages = [], [], [], []
    services = SimpleNamespace(
        settings=SimpleNamespace(guard_mode="enforce"),
        log=SimpleNamespace(append=lambda kind, *, payload: events.append((kind, payload))),
    )
    request = SimpleNamespace(screen={}, page=None)
    prepared = decision.DecisionPromptContext(
        state_factory=lambda: {"memo": "preserved"},
        capability=decision.ActionCapability(goal="synthetic", completion="synthetic"),
        active_fields=frozenset({"hbl_no"}),
    )

    def call(_services, purpose, prompt, schema, *, page):
        requests.append((purpose, prompt, schema, page))
        return corrected

    def evidence(_services, response, screen):
        stages.append("evidence")
        return response

    monkeypatch.setattr(decision, "_call_screen_decision", call)
    monkeypatch.setattr(decision, "bind_value_evidence_to_controls", evidence)
    monkeypatch.setattr(
        decision, "write_decision_artifact", lambda _s, name, value: artifacts.append((name, value))
    )
    for reject_correction in (False, True):
        events.clear()
        requests.clear()
        artifacts.clear()
        stages.clear()

        def prepare(_request, _services, response, capability, fields, reject=reject_correction):
            assert capability is prepared.capability
            assert fields == {"hbl_no"}
            if response is original:
                raise ValueError(error)
            stages.append("validate")
            if reject:
                raise ValueError("synthetic_invalid_correction")
            return response

        monkeypatch.setattr(decision, "prepare_screen_plan", prepare)
        if reject_correction:
            with pytest.raises(ValueError, match="synthetic_invalid_correction"):
                decision.validate_or_replan(original, request, services, prepared)
            assert not artifacts and not events, error
        else:
            result = decision.validate_or_replan(original, request, services, prepared)
            assert result is corrected, error
            assert artifacts == [(artifact, corrected)], error
            assert events == [
                (
                    "verify",
                    {"check": check, "rejected_target_ids": [], "corrected_target_ids": [4]},
                )
            ], error
        assert stages == ["evidence", "validate"], error
        assert len(requests) == 1, error
        purpose, prompt, schema, page = requests[0]
        assert (purpose, schema, page) == ("replan_after_rejection", decision.ScreenMatch, None)
        prefix = CONTEXT_MAPPING_RULES
        assert prompt.startswith(prefix), error
        payload = json.loads(prompt[len(prefix) :])
        assert payload["memo"] == "preserved"
        assert payload["correction"]["error"] == error
        assert instruction in payload["correction"]["instruction"]
        assert payload["correction"]["rejected_fields"] == []
        assert "rejected_target_ids" not in payload["correction"]
        assert original.steps == [] and prepared.state == {"memo": "preserved"}


def test_synthetic_second_observation_recovery_validates_its_replacement_action(
    tmp_path, monkeypatch
):
    # Synthetic named responses, not a pilot replay: completing an empty plan loses
    # a required field, then its recovery changes the next button to disabled.
    record = make_record()
    initial = named_response(steps=[])
    initial["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    button = {"role": "button", "bbox": [250, 15, 80, 30]}
    action = {"target": "Continue", "action": "click", "intent": "navigate", "point": [280, 30]}
    completion = named_response(fields={"Continue": button}, steps=[action])
    recovery = named_response(
        fields={**initial["fields"], "Continue": {**button, "enabled": False}}, steps=[action]
    )
    brain, model, *_ = setup_brain(tmp_path, record, [initial, completion, recovery])
    request, services = matching_contract(brain)
    request = replace(
        request,
        required_verification=(
            {
                "source": "hbl_no",
                "screen": "query",
                "descriptor": {"role": "edit", "name": "Reference"},
                "input_region": [100, 15, 150, 30],
            },
        ),
    )
    prepared_responses = []
    prepare = decision.prepare_screen_plan

    def capture_prepare(request, services, response, capability, active_fields):
        prepared_responses.append(response)
        return prepare(request, services, response, capability, active_fields)

    monkeypatch.setattr(decision, "prepare_screen_plan", capture_prepare)

    with pytest.raises(ValueError, match="screen_match_action_target_uncertain"):
        decision.decide_screen(request, services)

    assert len(model.prompts) == 3
    assert len(prepared_responses) == 2
    assert prepared_responses[0].steps == []
    final = prepared_responses[-1]
    assert final.mapping.fields["Continue"].enabled is False
    assert final.mapping.fields["Reference"].value == record.fields.hbl_no.value
    assert final.steps[0].action == "click"
    events = brain.log.read_all()
    assert [
        event.payload["context"]["purpose"] for event in events if event.kind == "model_call"
    ] == ["screen_decision", "missing_actions_recovery", "target_grounding"]
    assert not any(event.kind == "act" for event in events)


def test_synthetic_resized_frame_keeps_explicit_current_input_evidence(tmp_path):
    # Synthetic response: window resize invalidates old geometry, not the model's
    # explicit current field/source binding. No real model or desktop is involved.
    record = make_record()
    raw = named_response(steps=[])
    raw["fields"]["Reference"]["value"] = record.fields.hbl_no.value
    brain, model, *_ = setup_brain(tmp_path, record, [])
    brain.settings = brain.settings.model_copy(
        update={"visual_model_led_semantics": True, "visual_model_only_review": False}
    )
    screen = {"ocr": [], "markers": [], "image_size": [800, 200]}
    request, services = matching_contract(brain, screen)
    request = replace(
        request,
        required_verification=(
            {
                "source": "hbl_no",
                "screen": "query",
                "descriptor": {"role": "edit", "name": "Reference"},
                "input_region": [100, 15, 150, 30],
                "input_image_size": [400, 200],
            },
        ),
    )
    mapping = decision.ScreenMapping.model_validate(raw)
    response = decision.ScreenMatch.model_validate(
        mapping.local_payload(screen, {"hbl_no": record.fields.hbl_no.value})
    )
    prepared = decision.DecisionPromptContext(
        state_factory=lambda: {},
        capability=decision.ActionCapability(goal="synthetic", completion="synthetic"),
        active_fields=frozenset({"hbl_no"}),
    )

    assert decision.retain_screen_observation(response, request, services, prepared) is response
    assert model.prompts == []
    assert response.evidence[0].field == "hbl_no"
