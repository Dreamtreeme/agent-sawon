"""Provider adapters must preserve the same workflow context and verified facts."""

import json

import pytest
from test_brain import record as record
from test_jev_matching import build_request
from test_screen_matching import fixture, response, setup_brain
from test_visual_scene import wire

from brain.graph.context import execution_context
from brain.graph.matching import RULES, match_screen
from brain.graph.schemas import Choice, Decision
from brain.visual.markers import build_markers, ground_meanings
from brain.visual.scene import bind_scene


@pytest.mark.parametrize(
    "phase,step,verified,submitted",
    [
        ("duplicate_query", "enter-duplicate-key", False, False),
        ("final_query", "search-final", True, False),
        ("final_query", "verify-final", True, True),
        ("save_result", "inspect-save", False, False),
    ],
)
def test_basic_gemini_and_jev_receive_identical_execution_context(
    tmp_path, record, phase, step, verified, submitted
):
    blocks, boxes, _, meanings = fixture()
    markers = build_markers(blocks, boxes, 400, 200)
    brain, model, *_ = setup_brain(
        tmp_path, record, [response(meanings, procedure_step=step, steps=[], operation="stop")]
    )
    brain.observation = bind_scene(wire(), blocks, ground_meanings(meanings, markers, blocks))
    brain.phase = phase
    brain.query_value_verified = verified
    brain.query_submitted = submitted
    expected = execution_context(brain)
    direct = brain._decision_prompt().split("TRUSTED_TASK=", 1)[1].split("\nUI_TEXT=", 1)[0]
    direct = json.loads(direct)
    assert direct["phase_instruction"] == expected["guidance"]
    assert direct["execution_policy"]["current_step"] == expected["checkpoint"]
    assert (
        sorted({c["intent"] for c in direct["execution_policy"]["allowed_next_choices"]})
        == expected["allowed_intents"]
    )
    assert direct["final_required_fields"] == expected["final_required_fields"]
    match_screen(
        brain,
        {
            "ocr": [b.model_dump() for b in blocks],
            "markers": [{**m.model_dump(), "contained_ocr_ids": list(m.text_ids)} for m in markers],
        },
        None,
    )
    matched = json.loads(model.prompts[-1][len(RULES) :])
    assert matched["execution"] == expected
    request = build_request(matched)
    assert request.state["execution"] == expected
    field_questions = [q for k, q in request.questions.items() if k.startswith("field:")]
    assert field_questions
    assert all(q.instructions["query_input"] == expected["query_input"] for q in field_questions)


def test_pending_proposal_does_not_mutate_actual_facts_or_runtime_policy(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "final_query"
    brain.decision = Decision(
        screen_kind="query",
        operation="act",
        intent="query_input",
        subgoal="query",
        reason="query",
        action=Choice(type="set_text", ref="e1", source="hbl_no"),
    )
    brain.pending = {"decision": brain.decision}
    actual = execution_context(brain)
    proposed = execution_context(brain, anticipate=True)
    assert actual["checkpoint"] == "query_input"
    assert proposed["checkpoint"] == "search" and proposed["conditional"]
    assert proposed["query_input"]["pending"] and not proposed["query_input"]["required"]
    assert not brain.query_value_verified
    assert brain._decision_policy().current_step == "query_input"


def test_pending_last_record_fill_proposes_save_without_mutating_verified_fields(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "enter"
    brain.duplicate_clear = True
    last_field = "consignee"
    brain.verified = {field: f"ref-{field}" for field in brain.values if field != last_field}
    brain.decision = Decision(
        screen_kind="editor",
        operation="act",
        intent="fill",
        subgoal="fill-entry",
        reason="fill final field",
        action=Choice(type="set_text", ref="e1", source=last_field),
    )
    brain.pending = {"decision": brain.decision}

    actual = execution_context(brain)
    proposed = execution_context(brain, anticipate=True)

    assert actual["checkpoint"] == "fill_fields"
    assert proposed["checkpoint"] == "save"
    assert proposed["conditional"]
    assert proposed["remaining_fields"] == []
    assert proposed["pending_actions"] == [{"intent": "fill", "source": last_field}]
    assert proposed["allowed_intents"] == [
        "cancel",
        "navigate",
        "save",
        "select_tab",
        "stop",
        "wait",
    ]
    assert last_field not in brain.verified
    assert brain._decision_policy().current_step == "fill_fields"


def test_pending_login_proposes_post_authentication_work_without_resubmitting_credentials(
    tmp_path, record
):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.credentials = {
        "credential.username": "fixture-user",
        "credential.password": "fixture-password",
    }
    brain.decision = Decision(
        screen_kind="login",
        operation="act",
        intent="login",
        subgoal="login",
        reason="submit credentials",
        action=Choice(type="click", ref="e1"),
    )
    brain.pending = {"decision": brain.decision}

    actual = execution_context(brain)
    proposed = execution_context(brain, anticipate=True)

    assert "credential" in actual["allowed_intents"]
    assert proposed["conditional"]
    assert "credential" not in proposed["allowed_intents"]
    assert "login" not in proposed["allowed_intents"]
    assert "Check for an existing record" in proposed["guidance"]
    assert brain._decision_policy().facts.available_credential_sources
