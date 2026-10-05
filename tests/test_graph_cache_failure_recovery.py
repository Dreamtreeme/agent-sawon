"""Full graph failure and recovery with run-local mappings on a changed layout."""

from __future__ import annotations

import json

from test_brain import decision, run_brain, success_steps, text_choice, values_for
from test_brain import record as record
from test_graph_cache_rebinding import ChangedEditorRelay, settings

from brain.cache import SemanticCache


class MovingEditorRelay(ChangedEditorRelay):
    """Keep semantic controls stable while every editor observation changes refs/order."""

    def __init__(self, record, *, failing_field=None, failed_attempts=0, bad_final_row=False):
        super().__init__(record)
        self.current_refs = dict(self.field_refs)
        self.failing_field = failing_field
        self.failed_attempts = failed_attempts
        self.bad_final_row = bad_final_row
        self.input_attempts = []
        self.current_obs_id = None

    def observe(self):
        observation = super().observe()
        if self.mode == "editor":
            remap = {
                ref: f"e{10000 + self.calls * 100 + index}"
                for index, ref in enumerate(self.field_refs.values())
            }
            self.current_refs = {field: remap[ref] for field, ref in self.field_refs.items()}
            for item in observation.elements:
                item.ref = remap.get(item.ref, item.ref)
            offset = self.calls % len(observation.elements)
            observation.elements = observation.elements[offset:] + observation.elements[:offset]
            if self.calls % 2:
                observation.elements.reverse()
        if self.bad_final_row and self.saved and self.mode == "query":
            next(item for item in observation.elements if item.ref == "e42").value = "MISMATCH"
        self.current_obs_id = observation.obs_id
        return observation

    def act(self, action):
        item = action.root
        field = None
        if self.mode == "editor" and item.type in {"set_text", "type_text"}:
            field = next(key for key, ref in self.current_refs.items() if ref == item.target.ref)
            self.input_attempts.append((field, item.target.ref, str(self.current_obs_id)))
        saved_refs, self.field_refs = self.field_refs, self.current_refs
        try:
            response = super().act(action)
        finally:
            self.field_refs = saved_refs
        if field == self.failing_field and self.failed_attempts:
            self.failed_attempts -= 1
            self.inputs[field] = ""
        return response


def batch_proposal(relay):
    def propose(prompt):
        data = json.loads(prompt.split("MAPPING_DATA=", 1)[1])
        return {
            "observation_id": data["observation_id"],
            "bindings": [
                {"field": entry["field"], "ref": relay.current_refs[entry["field"]]}
                for entry in data["learned_fields"]
            ],
        }

    return propose


def test_temporary_failure_invalidates_locally_then_recovers_on_fresh_model_target(
    tmp_path, record, graph_cache_snapshot
):
    path = tmp_path / "cache.json"
    path.write_bytes(graph_cache_snapshot)
    original_bytes = graph_cache_snapshot
    original = {entry.entry_id: entry for entry in SemanticCache(path).entries()}

    for field_index, max_retries, expected_mode in (
        (0, 0, "label_anchor"),
        (6, 1, "model_rebind"),
    ):
        path.write_bytes(original_bytes)
        failing_field = list(values_for(record))[field_index]
        relay = MovingEditorRelay(
            record, failing_field=failing_field, failed_attempts=max_retries + 1
        )

        def model_fallback(prompt, failing_field=failing_field, relay=relay):
            # Earlier successful local proposals are still staged; failures cannot
            # mutate the source snapshot while the business run is incomplete.
            assert path.read_bytes() == original_bytes
            assert "semantic_cache" in prompt
            assert "input_value_mismatch" in prompt
            return decision(
                intent="fill",
                kind="editor",
                action=text_choice(failing_field, relay.current_refs[failing_field]),
            )

        steps = [step for step in success_steps(record) if step["intent"] != "fill"]
        steps[4:4] = [batch_proposal(relay), model_fallback]
        run_settings = settings(path)
        run_settings = run_settings.model_copy(
            update={"cache": run_settings.cache.model_copy(update={"max_retries": max_retries})}
        )
        result, brain, _, model = run_brain(
            tmp_path / f"warm-{expected_mode}",
            record,
            steps,
            settings=run_settings,
            relay=relay,
        )
        assert result.status == "success", (expected_mode, result)
        assert relay.inputs == values_for(record)
        assert brain.cache_rebind_calls == 1
        events = brain.log.read_all()
        failures = [
            event
            for event in events
            if event.kind == "cache_miss" and event.payload.get("reason") == "input_value_mismatch"
        ]
        assert [event.payload["invalidated"] for event in failures] == [False] * max_retries + [
            True
        ]
        failed_ids = {event.payload["entry_id"] for event in failures}
        assert len(failed_ids) == 1 and failed_ids.isdisjoint(original)
        assert {
            event.payload["match_mode"]
            for event in events
            if event.kind == "cache_hit" and event.payload["entry_id"] in failed_ids
        } == {expected_mode}
        attempts = [attempt for attempt in relay.input_attempts if attempt[0] == failing_field]
        assert len(attempts) == max_retries + 2
        assert len({ref for _, ref, _ in attempts}) == len(attempts)
        assert len({obs_id for _, _, obs_id in attempts}) == len(attempts)
        selections = [
            event
            for event in events
            if event.kind == "select"
            and event.payload["decision"]["intent"] == "fill"
            and event.payload["decision"]["action"]["source"] == failing_field
        ]
        assert [event.payload["source"] for event in selections] == ["semantic_cache"] * (
            max_retries + 1
        ) + ["model"]
        assert [event.payload["obs_id"] for event in selections] == [obs for _, _, obs in attempts]
        saved = {entry.entry_id: entry for entry in SemanticCache(path).entries()}
        assert len(saved) == 22
        assert all(saved[entry_id] == entry for entry_id, entry in original.items())
        assert all(saved[entry_id].failure_count == 0 for entry_id in failed_ids)
        assert len([event for event in events if event.kind == "model_call"]) == len(model.prompts)


def test_final_business_mismatch_discards_all_staged_cross_layout_mappings(
    tmp_path, record, graph_cache_snapshot
):
    path = tmp_path / "cache.json"
    path.write_bytes(graph_cache_snapshot)
    original = graph_cache_snapshot
    relay = MovingEditorRelay(record, bad_final_row=True)
    steps = [step for step in success_steps(record) if step["intent"] != "fill"]
    steps.insert(4, batch_proposal(relay))
    steps[-1] = decision("stop", intent="stop")
    result, brain, _, _ = run_brain(
        tmp_path / "warm", record, steps, settings=settings(path), relay=relay
    )
    assert result.status == "stopped" and result.reason == "model_requested_stop"
    assert relay.saved and brain.query_submitted
    assert relay.inputs == values_for(record)
    events = brain.log.read_all()
    assert len([event for event in events if event.kind == "cache_hit"]) == 11
    assert brain.cache_rebind_calls == 1
    reviews = [
        event.payload
        for event in events
        if event.kind == "verify" and event.payload.get("check") == "current_evidence_review"
    ]
    assert reviews[-1]["status"] == "needs_more"
    assert reviews[-1]["missing_fields"] == ["shipper"]
    promotions = [
        event.payload
        for event in events
        if event.kind == "verify" and event.payload.get("check") == "semantic_cache_promotion"
    ]
    assert len(promotions) == 1
    assert promotions[0]["promoted"] == 0 and promotions[0]["business_success"] is False
    assert brain.cache_session.closed
    assert not brain.cache_session.proposed_entries and not brain.cache_session.rebound
    assert path.read_bytes() == original
