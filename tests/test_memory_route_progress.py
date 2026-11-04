"""Synthetic route arrivals using existing fake screens and real runtime functions."""

from copy import deepcopy

import pytest
from test_memory_retrieval import setup, synthetic_ordered_route, synthetic_recovery_graph

from brain.cache.methods import MethodAction, MethodDocument, MethodTarget
from brain.graph.screen_decision import ScreenMatch
from sawon_runtime.storage import atomic_write_json


def test_synthetic_model_arrival_resumes_inside_a_taught_step_without_fake_delivery(tmp_path):
    brain, model, runtime, method, query, detail, screen, _ = synthetic_recovery_graph(tmp_path)
    # A menu detour and the query arrival belong to ONE taught step, still unfinished.
    menu = query.model_copy(update={"id": "menu-detour", "trace_index": 0})
    query = query.model_copy(update={"trace_index": 1, "predecessor_group_ids": [menu.id]})
    detail = detail.model_copy(update={"trace_index": 2, "taught_step_id": query.taught_step_id})
    atomic_write_json(
        runtime.library.path,
        MethodDocument(
            methods=[method.model_copy(update={"action_groups": [menu, query, detail]})]
        ),
    )
    groups = runtime.prompt_context()["methods"][0]["groups"]
    assert [group["id"] for group in groups] == [menu.id, query.id, detail.id]
    assert groups[-1]["actions"] and "expected_result" in groups[-1]
    assert runtime.match_pattern(deepcopy(screen)) is None

    # Synthetic named provider reply passes through the real projection/persistence boundary.
    raw = ScreenMatch.model_validate(
        {
            "screen_kind": "editor",
            "fields": {},
            "method_id": method.id,
            "resume_group_id": detail.id,
            "observed_step_results": [],
            "steps": [],
        }
    )
    mapped = raw.mapping
    response = ScreenMatch.model_validate(mapped.local_payload(screen)).model_copy(
        update={"mapping": mapped}
    )
    assert response.accepted_mapping(screen).resume_group_id == detail.id
    runtime.remember(screen, response)

    assert runtime.state.history[-1]["resumed_group_ids"] == [menu.id, query.id]
    assert runtime.state.history[-1]["resume_group_id"] == detail.id
    assert not runtime.state.used
    assert not brain.completed_taught_steps
    assert runtime.match_pattern(screen) is not None
    assert runtime.state.pending["group_id"] == detail.id
    assert model.prompts == []


@pytest.mark.parametrize("selection", ["cached", "unknown", "first", "already-passed"])
def test_synthetic_noncurrent_or_nonadvancing_cursor_does_not_skip_work(tmp_path, selection):
    _, _, runtime, method, query, detail, screen, _ = synthetic_recovery_graph(tmp_path)
    group_id = detail.id
    if selection == "unknown":
        group_id = "group-from-another-route"
    elif selection == "first":
        group_id = query.id
    elif selection == "already-passed":
        runtime.state.used.add((method.id, query.id))
        group_id = query.id
    before = [group.id for group in runtime._remaining_groups(method)]
    runtime.remember(
        screen,
        ScreenMatch(
            screen_kind="editor",
            controls=[],
            procedure_step="task-action",
            method_id=method.id,
            resume_group_id=group_id,
            steps=[],
        ),
        allow_recovery=selection != "cached",
    )
    assert [group.id for group in runtime._remaining_groups(method)] == before
    assert not any(item.get("resumed_group_ids") for item in runtime.state.history)


def test_actual_delivery_failure_calls_model_once_before_resuming_route(tmp_path):
    brain, model, relay = setup(tmp_path, fail_second=True)
    runtime = brain.memory_retrieval
    method = runtime.library.methods()[0]
    first = method.action_groups[0].model_copy(
        update={"screen_path": ["Record editor"], "trace_index": 0}
    )
    target = MethodTarget(name="Next", role="button", bbox=(300, 140, 80, 30), point=(340, 155))
    second = first.model_copy(
        update={
            "id": "next-screen",
            "trace_index": 1,
            "predecessor_group_ids": [first.id],
            "targets": [target],
            "actions": [
                MethodAction(target="Next", action="click", intent="navigate", point=target.point)
            ],
        }
    )
    method = method.model_copy(update={"action_groups": [first, second]})
    atomic_write_json(runtime.library.path, MethodDocument(methods=[method]))
    brain._observe()
    assert model.prompts == []
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert relay.entered == [brain.values["customer"]]
    assert runtime.recovery_required
    assert runtime.state.history[-1]["status"] == "interrupted"
    assert not runtime.state.used
    brain._observe()
    assert len(model.prompts) == 1
    assert brain._select_node() == "review"
    assert brain._review_node() == "execution"
    assert brain._act_node() == "observation"
    assert not runtime.recovery_required
    assert brain._verify_node() == "review"
    assert len(model.prompts) == 1
    assert runtime.state.pending["group_id"] == second.id
    assert any(item.get("replaced_edge") == (method.id, first.id) for item in runtime.state.history)


def test_actual_verification_failure_discards_prebound_replay_and_preserves_failed_edge(tmp_path):
    brain, _, runtime, method, first, second, screen, _ = synthetic_ordered_route(tmp_path)
    assert runtime.match_pattern(deepcopy(screen)) is not None
    runtime.delivered(complete=True)
    next_screen = {**deepcopy(screen), "screen_phash": second.screen_phash}
    brain.visual_proposal = runtime.match_pattern(next_screen)
    assert brain.visual_proposal is not None
    assert runtime.state.pending["group_id"] == second.id

    assert brain._fail("screen_fill_value_mismatch") == "observation"
    runtime.verify_delivery(success=False)

    assert brain.visual_proposal is None
    assert runtime.state.pending is None
    assert runtime.recovery_required
    assert (method.id, first.id) not in runtime.state.used
    assert runtime.state.history[-1]["status"] == "failed"
    assert runtime.match_pattern(next_screen) is None
    assert runtime._remaining_groups(method)[0].id == first.id


def test_unchanged_effect_without_runtime_failure_keeps_replay_available(tmp_path):
    _, _, runtime, method, first, _, screen, _ = synthetic_ordered_route(tmp_path)
    assert runtime.match_pattern(deepcopy(screen)) is not None
    runtime.delivered(complete=True)

    runtime.verify_delivery(success=False)

    assert not runtime.recovery_required
    assert (method.id, first.id) in runtime.state.used


@pytest.mark.parametrize("complete", [False, True], ids=["interrupted", "delivered"])
@pytest.mark.parametrize("source", ["replay", "model-recovery", "cache", "suffix"])
def test_only_complete_model_recovery_delivery_reopens_suspended_route(tmp_path, complete, source):
    brain, _, runtime, _, _, second, screen, _ = synthetic_ordered_route(tmp_path)
    original = runtime.match_pattern(deepcopy(screen))
    assert original is not None
    if source == "suffix":
        runtime.remember(
            deepcopy(screen),
            original.model_copy(update={"method_id": None, "steps": original.steps[:1]}),
        )
    runtime.delivered(complete=True)
    if source in {"model-recovery", "cache"}:
        # Observation prepares a current model repair before effect review of
        # the previous delivery discovers its missing input evidence.
        response = original.model_copy(update={"method_id": None})
        runtime.remember(deepcopy(screen), response, allow_recovery=source == "model-recovery")
        assert runtime.state.pending["origin"] == "adaptation"
    elif source == "suffix":
        response = runtime.match_pattern(deepcopy(screen))
        assert response is not None
        assert [step.source for step in response.steps] == ["reference"]
        assert runtime.state.pending["origin"] == "adaptation"
    else:
        response = runtime.match_pattern({**deepcopy(screen), "screen_phash": second.screen_phash})
        assert runtime.state.pending["origin"] == "reuse"
    brain.visual_proposal = response

    assert brain._fail("screen_fill_evidence_missing") == "observation"
    runtime.verify_delivery(success=False)
    assert runtime.recovery_required
    if source == "model-recovery":
        assert brain.visual_proposal is response
        assert runtime.state.pending["origin"] == "adaptation"
    else:
        assert brain.visual_proposal is None and runtime.state.pending is None

    runtime.delivered(complete=complete)

    assert runtime.recovery_required is not (source == "model-recovery" and complete)
