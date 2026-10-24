"""Synthetic model replies; real plan selection, composition and validation."""

from dataclasses import replace

import pytest
from test_brain import record as record
from test_screen_decision import matching_contract, setup_brain

from brain.graph import screen_decision as decision
from brain.visual.scene import SemanticDetection


@pytest.mark.parametrize("route", ["grounded", "replan"])
@pytest.mark.parametrize("full_interpretation", [False, True], ids=["plan", "full-match"])
def test_synthetic_composition_preserves_mapping_and_uses_new_plan(
    tmp_path, record, route, full_interpretation
):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "task"
    screen = {
        "markers": [{"id": 1, "source": "omniparser", "bbox": [0, 0, 40, 40]}],
        "ocr": [],
    }
    request, services = matching_contract(brain, screen)
    meaning = SemanticDetection(
        screen_kind="editor",
        controls=[{"id": 1, "role": "button", "confidence": 1}],
        uncertainties=["synthetic context"],
        needs_refinement=True,
    )
    plan = decision.ScreenPlan.model_validate(
        {
            "fields": {"Next": {"role": "button", "bbox": [0, 0, 40, 40]}},
            "steps": [
                {"target": "Next", "action": "click", "intent": "navigate", "point": [10, 10]}
            ],
            "purpose": "new plan",
            "expected_result": "next screen",
            "observed_step_results": [
                {"step_id": "observed-step", "observed_result": "visible result", "completed": True}
            ],
            "needs_refinement": False,
            "evidence": [{"field": "hbl_no", "target": "Next"}],
        }
    )
    if full_interpretation:
        # decide_screen.traced_call also returns a full ScreenMatch to ScreenPlan callers.
        plan = decision.ScreenMatch.model_validate(
            {
                **plan.model_dump(),
                "screen_kind": "dialog",
                "controls": [{"id": 1, "role": "button", "confidence": 0.95}],
                "uncertainties": ["new interpretation"],
            }
        ).model_copy(update={"mapping": plan.mapping})
    snapshots = meaning.model_dump(), plan.model_dump(), plan.mapping.model_dump()
    calls = []

    def fake_model(prompt, schema, *, page):
        calls.append(prompt)
        assert schema is decision.ScreenPlan
        return plan

    services = replace(services, call=fake_model)
    request = replace(request, grounded_interpretation=meaning)
    prepared = decision.build_decision_prompt(request, services)
    if route == "grounded":
        selected = decision.select_screen_response(request, services, prepared)
        response = decision.validate_or_replan(selected.response, request, services, prepared)
    else:
        # A real invalid click (missing point) triggers the real replan branch.
        invalid = decision.ScreenMatch.model_validate(
            {
                **meaning.model_dump(),
                "procedure_step": "task-action",
                "purpose": "old plan",
                "steps": [{"target": 1, "action": "click", "intent": "navigate"}],
            }
        )
        response = decision.validate_or_replan(invalid, request, services, prepared)
        assert invalid.purpose == "old plan" and invalid.steps[0].point is None
    assert len(calls) == 1
    assert response.mapping is plan.mapping
    expected_meaning = plan if full_interpretation else meaning
    assert response.controls == expected_meaning.controls
    assert response.screen_kind == expected_meaning.screen_kind
    assert response.uncertainties == expected_meaning.uncertainties
    assert response.needs_refinement is False
    assert response.steps == plan.steps
    assert response.purpose == "new plan"
    assert response.expected_result == "next screen"
    assert [item.model_dump() for item in response.observed_step_results] == [
        {"step_id": "observed-step", "observed_result": "visible result", "completed": True}
    ]
    assert response.evidence == plan.evidence
    assert snapshots == (meaning.model_dump(), plan.model_dump(), plan.mapping.model_dump())
