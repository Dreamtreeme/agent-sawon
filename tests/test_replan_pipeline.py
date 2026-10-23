"""Synthetic boundary responses exercise the common correction pipeline."""

import json
from types import SimpleNamespace

import pytest

from brain.graph import screen_decision as decision


@pytest.mark.parametrize(
    ("error", "artifact", "check", "instruction"),
    [
        (
            "screen_match_action_surface_mismatch",
            "action-surface-correction",
            "action_surface_corrected",
            "allowed_actions",
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
        state={"memo": "preserved"},
        capability=decision.ActionCapability(goal="synthetic", completion="synthetic"),
        active_fields=frozenset({"hbl_no"}),
        prompt="unused",
        guard_rules="guard\n",
        taught_rules="education\n",
        method_rules="memory\n",
    )

    def call(_services, purpose, prompt, schema, *, page):
        requests.append((purpose, prompt, schema, page))
        return corrected

    def local(response, screen):
        stages.append("local")
        return response

    def evidence(_services, response, screen):
        stages.append("evidence")
        return response

    monkeypatch.setattr(decision, "_call_screen_decision", call)
    monkeypatch.setattr(decision, "local_response", local)
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
        assert stages == ["local", "evidence", "validate"], error
        assert len(requests) == 1, error
        purpose, prompt, schema, page = requests[0]
        assert (purpose, schema, page) == ("replan_after_rejection", decision.ScreenMatch, None)
        prefix = decision.SCREEN_DECISION_RULES + "guard\neducation\nmemory\n"
        assert prompt.startswith(prefix), error
        payload = json.loads(prompt[len(prefix) :])
        assert payload["memo"] == "preserved"
        assert payload["correction"]["error"] == error
        assert instruction in payload["correction"]["instruction"]
        assert original.steps == [] and prepared.state == {"memo": "preserved"}
