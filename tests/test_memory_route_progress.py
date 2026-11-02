"""Synthetic route arrivals using existing fake screens and real runtime functions."""

from copy import deepcopy

import pytest
from test_memory_retrieval import synthetic_recovery_graph

from brain.cache.methods import MethodDocument
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
