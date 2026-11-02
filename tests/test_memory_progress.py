"""Synthetic split-action recoveries use existing fake capture/model fixtures."""

from copy import deepcopy
from dataclasses import replace

import pytest
from test_memory_retrieval import synthetic_ordered_route

from brain.cache.methods import MethodAction, MethodDocument
from brain.graph.screen_mapping import ScreenMapping
from sawon_runtime.storage import atomic_write_json


def synthetic_scroll_route(tmp_path):
    # Synthetic version of an input batch followed by a wheel action; no desktop/API.
    _, model, runtime, method, first, second, screen, _ = synthetic_ordered_route(tmp_path)
    first = first.model_copy(
        update={
            "screen_path": ["Record editor"],
            "actions": [
                *first.actions,
                MethodAction(
                    target=first.targets[0].name,
                    action="key",
                    intent="navigate",
                    keys="pagedown",
                    point=(390, 100),
                ),
            ],
        }
    )
    method = method.model_copy(update={"action_groups": [first, second]})
    atomic_write_json(runtime.library.path, MethodDocument(methods=[method]))
    response = runtime.match_pattern(deepcopy(screen))
    assert response is not None and response.mapping is not None
    return model, runtime, method, first, second, screen, response


def synthetic_slice(response, start, end):
    # The fake model emits only this action slice in both local and semantic forms.
    return response.model_copy(
        update={
            "method_id": None,
            "steps": response.steps[start:end],
            "mapping": response.mapping.model_copy(
                update={"steps": response.mapping.steps[start:end]}
            ),
        }
    )


@pytest.mark.parametrize("redact", [None, "purpose", "expected_result", "observed_step_results"])
def test_synthetic_learning_revalidates_only_redacted_mapping(tmp_path, monkeypatch, redact):
    _, runtime, _, first, _, screen, response = synthetic_scroll_route(tmp_path)
    runtime.task_context = replace(
        runtime.task_context, credentials={"credential.password": "private-session-secret"}
    )
    payload = response.mapping.model_dump(exclude_unset=True)
    if redact == "observed_step_results":
        payload[redact] = [
            dict(
                step_id=first.taught_step_id,
                completed=False,
                observed_result="private-session-secret is pending",
            )
        ]
    elif redact is not None:
        payload[redact] = "private-session-secret is pending"
    # An explicit empty reading and an unobserved field must remain distinguishable.
    field = next(iter(payload["fields"]))
    payload["fields"][field]["value"] = ""
    mapping = ScreenMapping.model_validate(payload)
    response = response.model_copy(update={"mapping": mapping})
    before = mapping.model_dump(exclude_unset=True)
    validations = []
    validate_mapping = ScreenMapping.model_validate

    def validate(value, *args, **kwargs):
        validations.append(value)
        return validate_mapping(value, *args, **kwargs)

    monkeypatch.setattr(ScreenMapping, "model_validate", validate)
    runtime.remember(deepcopy(screen), response)

    assert len(validations) == int(redact is not None)
    assert mapping.model_dump(exclude_unset=True) == before
    assert "value" in mapping.fields[field].model_fields_set
    assert any("value" not in item.model_fields_set for item in mapping.fields.values())
    learned = runtime.state.pending["method"]
    assert "private-session-secret" not in learned.model_dump_json()
    if redact is not None:
        assert "{credential.password}" in learned.model_dump_json()


@pytest.mark.parametrize("batch_sizes", [(3,), (2, 1)], ids=["whole", "split-fill-and-scroll"])
def test_synthetic_split_batch_and_shifted_wheel_preserve_original_progress(tmp_path, batch_sizes):
    _, runtime, method, first, second, screen, response = synthetic_scroll_route(tmp_path)
    start = 0
    for size in batch_sizes:
        end = start + size
        recovery = synthetic_slice(response, start, end)
        if end == 3:
            # Wheel context is the input field; its physical point is elsewhere.
            recovery.steps[-1] = recovery.steps[-1].model_copy(update={"point": [385, 175]})
            recovery.mapping.steps[-1] = recovery.mapping.steps[-1].model_copy(
                update={"point": [385, 175]}
            )
        runtime.remember(deepcopy(screen), recovery)
        assert runtime.state.pending["action_progress"] == {
            "group_id": first.id,
            "start": start,
            "end": end,
        }
        assert runtime.state.pending["replaced_edge"] == (
            (method.id, first.id) if end == 3 else None
        )
        runtime.delivered(complete=True)
        runtime.verify_delivery(success=True)
        if end < 3:
            assert runtime._remaining_groups(method)[0].id == first.id
            memory = runtime.retrieve_screen_memory(deepcopy(screen))
            assert [(step["action"], step["keys"]) for step in memory["procedure"][0]["steps"]] == [
                ("key", "pagedown")
            ]
        start = end

    assert runtime._remaining_groups(method)[0].id == second.id
    assert (method.id, first.id) not in runtime.state.used, "recovery is not original delivery"
    assert runtime.state.history[-1]["replaced_edge"] == (method.id, first.id)


@pytest.mark.parametrize("effect", [False, None], ids=["no-effect", "unknown-effect"])
def test_synthetic_unverified_prefix_cannot_commit_original_action_progress(tmp_path, effect):
    _, runtime, method, first, _, screen, response = synthetic_scroll_route(tmp_path)
    runtime.remember(deepcopy(screen), synthetic_slice(response, 0, 1))
    runtime.delivered(complete=True)
    assert runtime._action_offset(method.id, first.id) == 1  # Next observation's provisional view.
    runtime.verify_delivery(success=effect)

    assert runtime._action_offset(method.id, first.id) == 0
    assert "action_progress" not in runtime.state.history[-1]
    assert "replaced_edge" not in runtime.state.history[-1]
    replay = runtime.match_pattern(deepcopy(screen))
    assert [step.source for step in replay.steps] == ["customer", "reference", None]


def test_synthetic_suffix_replay_and_promotion_do_not_restore_the_delivered_prefix(tmp_path):
    model, runtime, method, first, _, screen, response = synthetic_scroll_route(tmp_path)
    runtime.remember(deepcopy(screen), synthetic_slice(response, 0, 1))
    runtime.delivered(complete=True)
    runtime.verify_delivery(success=True)
    assert runtime._remaining_groups(method)[0].id == first.id

    replay = runtime.match_pattern(deepcopy(screen))
    assert [(step.action, step.source) for step in replay.steps] == [
        ("fill", "reference"),
        ("key", None),
    ]
    assert [(step.action, step.model_dump().get("source")) for step in replay.mapping.steps] == [
        ("fill", "reference"),
        ("scroll", None),
    ]
    assert [action.slot for action in runtime.state.pending["method"].action_groups[0].actions] == [
        "reference",
        None,
    ]
    runtime.delivered(complete=True)
    runtime.verify_delivery(success=True)
    assert runtime.state.history[-1]["replaced_edge"] == (method.id, first.id)
    assert runtime.finish(success=True) == 1

    promoted = runtime.library.methods()[0]
    assert [
        (action.action, action.slot, action.keys)
        for group in promoted.action_groups
        for action in group.actions
    ] == [
        ("fill", "customer", None),
        ("fill", "reference", None),
        ("key", None, "pagedown"),
    ]
    assert model.prompts == []


def test_synthetic_opposite_scroll_direction_does_not_finish_a_partially_delivered_group(tmp_path):
    _, runtime, method, first, _, screen, response = synthetic_scroll_route(tmp_path)
    runtime.remember(deepcopy(screen), synthetic_slice(response, 0, 2))
    runtime.delivered(complete=True)
    runtime.verify_delivery(success=True)
    recovery = synthetic_slice(response, 2, 3)
    recovery.steps[0] = recovery.steps[0].model_copy(update={"keys": "pageup"})
    recovery.mapping.steps[0] = recovery.mapping.steps[0].model_copy(update={"direction": "up"})
    runtime.remember(deepcopy(screen), recovery)
    assert runtime.state.pending["action_progress"] is None
    assert runtime.state.pending["replaced_edge"] is None
    runtime.delivered(complete=True)
    runtime.verify_delivery(success=True)
    assert runtime._remaining_groups(method)[0].id == first.id
    assert runtime._action_offset(method.id, first.id) == 2


def test_synthetic_suffix_prepared_before_failed_review_cannot_complete_the_missing_prefix(
    tmp_path,
):
    _, runtime, method, first, _, screen, response = synthetic_scroll_route(tmp_path)
    runtime.remember(deepcopy(screen), synthetic_slice(response, 0, 1))
    runtime.delivered(complete=True)
    # Real engine order: observe/prepare the next plan, then commit effect review.
    suffix = runtime.match_pattern(deepcopy(screen))
    assert [step.source for step in suffix.steps] == ["reference", None]
    runtime.verify_delivery(success=False)
    runtime.delivered(complete=True)
    runtime.verify_delivery(success=True)

    assert runtime._remaining_groups(method)[0].id == first.id
    assert runtime._action_offset(method.id, first.id) == 0
    assert "replaced_edge" not in runtime.state.history[-1]
    assert runtime.state.history[-1]["status"] == "delivered"


def test_synthetic_promotion_selects_delivered_variants_before_deduplication(tmp_path):
    _, runtime, method, first, second, _, _ = synthetic_scroll_route(tmp_path)
    obsolete = first.model_copy(update={"id": "unused-input", "trace_index": 0})
    first = first.model_copy(update={"trace_index": 1})
    retry = first.model_copy(
        update={"id": "input-retry", "trace_index": 2, "predecessor_group_ids": [first.id]}
    )
    second = second.model_copy(update={"trace_index": 3, "predecessor_group_ids": [retry.id]})
    candidate = method.model_copy(update={"action_groups": [obsolete, first, retry, second]})
    runtime._stage(candidate)
    runtime._stage(
        candidate.model_copy(
            update={"action_groups": [retry.model_copy(update={"purpose": "Repeated input"})]}
        )
    )
    assert [group.id for group in runtime.state.staged[method.id].action_groups] == [
        obsolete.id,
        first.id,
        retry.id,
        second.id,
    ]
    runtime.state.history.extend(
        dict(method_id=method.id, group_id=group.id, status="delivered")
        for group in (first, retry, second)
    )

    assert runtime.finish(success=True) == 1

    promoted = runtime.library.methods()[0]
    assert [group.id for group in promoted.action_groups] == [first.id, second.id]
    assert [group.trace_index for group in promoted.action_groups] == [0, 1]
    assert [group.predecessor_group_ids for group in promoted.action_groups] == [[], [first.id]]
    assert promoted.completion == method.completion
    # The runtime snapshot is unchanged by selection, deduplication and relinking.
    assert candidate.action_groups[-1].predecessor_group_ids == [retry.id]
