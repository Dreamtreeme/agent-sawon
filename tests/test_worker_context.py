"""Provider adapters must preserve the same workflow context and verified facts."""

import json

import pytest
from test_brain import record as record
from test_screen_matching import fixture, response, setup_brain
from test_visual_scene import wire

from brain.graph.context import execution_context
from brain.graph.matching import RULES, match_screen
from brain.graph.schemas import Choice, Decision
from brain.task_instruction import TaskInstruction
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


def test_generic_task_context_has_no_legacy_query_input_gate(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "task"
    brain.procedure = None
    brain.task_instruction = TaskInstruction(
        name="Arbitrary business task",
        objective="Use the supplied inputs to complete the user's work.",
        requirements=["Interpret the current application from visible evidence."],
        completion_conditions=["Visible evidence confirms the requested result."],
    )

    context = execution_context(brain)

    assert context["checkpoint"] == "task"
    assert "query_input" not in context
    assert context["remaining_inputs"] == sorted(brain.values)
    assert "remaining_fields" not in context
    assert "No application route is predefined" in context["guidance"]


def test_saved_review_context_excludes_run_local_provenance(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "final_query"
    brain.saved_evidence.begin("origin-a")
    brain.saved_evidence.query_row_seen = True
    brain.saved_evidence.fields["hbl_no"] = {"obs_id": "obs-a", "ref": "ref-a"}
    brain.saved_evidence.query_identity = {
        "obs_id": "obs-a",
        "capture_id": "capture-a",
        "screenshot": "run-a.png",
        "ref": "ref-a",
        "session_id": brain.saved_evidence.session_id,
    }
    first = execution_context(brain)["saved_record_review"]

    brain.saved_evidence.session_id = "different-session"
    brain.saved_evidence.origin_obs_id = "origin-b"
    brain.saved_evidence.query_identity = {
        "obs_id": "obs-b",
        "capture_id": "capture-b",
        "screenshot": "run-b.png",
        "ref": "ref-b",
        "session_id": "different-session",
    }
    second = execution_context(brain)["saved_record_review"]

    assert first == second
    assert first == {
        "record_id": brain.values["hbl_no"],
        "query_row_seen": True,
        "confirmed_fields": ["hbl_no"],
        "missing_fields": sorted(set(brain.values) - {"hbl_no"}),
        "conflict": None,
        "detail_identity_observed": False,
    }


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


def test_pending_external_selection_anticipates_its_verified_field(tmp_path, record):
    brain, *_ = setup_brain(tmp_path, record, [])
    brain.phase = "enter"
    brain.duplicate_clear = True
    selected_field = "shipper"
    brain.verified = {field: f"ref-{field}" for field in brain.values if field != selected_field}
    brain.decision = Decision(
        screen_kind="partner",
        operation="act",
        intent="select_option",
        subgoal="select-shipper",
        reason="select partner",
        action=Choice(type="click", ref="e1"),
    )
    brain.pending = {"decision": brain.decision, "effect_source": selected_field}

    actual = execution_context(brain)
    proposed = execution_context(brain, anticipate=True)

    assert actual["checkpoint"] == "fill_fields"
    assert proposed["checkpoint"] == "save"
    assert proposed["conditional"]
    assert proposed["remaining_fields"] == []
    assert selected_field not in brain.verified


def test_pending_external_selection_exposes_only_post_verification_steps(tmp_path, record):
    blocks, boxes, _, meanings = fixture()
    target = meanings.controls[0].id
    from brain.visual.marker_contract import model_controls

    controls = model_controls(
        meanings.controls, {"markers": [marker.model_dump() for marker in fixture()[2]]}
    )
    controls[0]["role"] = "button"
    combined = response(
        meanings,
        screen_kind="editor",
        procedure_step="open-additional-entry",
        controls=[controls[0]],
        steps=[
            {
                "target": target,
                "action": "click",
                "intent": "select_tab",
                "point": [175, 30],
            }
        ],
    )
    brain, model, *_ = setup_brain(tmp_path, record, [combined])
    brain.phase = "enter"
    brain.duplicate_clear = True
    basics = {"hbl_no", "mbl_no", "packages.count", "packages.unit", "pol"}
    brain.verified = {field: f"ref-{field}" for field in basics}
    brain.decision = Decision(
        screen_kind="partner",
        operation="act",
        intent="select_option",
        subgoal="select-shipper",
        reason="select partner",
        action=Choice(type="click", ref="e1"),
    )
    brain.pending = {"decision": brain.decision, "effect_source": "shipper"}
    markers = build_markers(blocks, boxes, 400, 200)
    matched = match_screen(
        brain,
        {
            "image_size": [400, 200],
            "ocr": [block.model_dump() for block in blocks],
            "markers": [
                {**marker.model_dump(), "contained_ocr_ids": list(marker.text_ids)}
                for marker in markers
            ],
        },
        None,
    )

    state = json.loads(model.prompts[-1][len(RULES) :])
    step_ids = {step["id"] for step in state["procedure"]}
    assert matched.procedure_step == "open-additional-entry"
    assert "open-additional-entry" in step_ids
    assert "open-shipper-lookup" not in step_ids
    assert state["notebook"]["anticipated_verified_fields"] == sorted(basics | {"shipper"})


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
