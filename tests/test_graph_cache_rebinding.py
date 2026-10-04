"""Measured batch mapping participates in the ordinary full graph, never direct input."""

import json

from test_brain import FakeRelay, ScriptedModel, element, run_brain, success_steps, values_for
from test_brain import record as record

from brain.cache import SemanticCache
from brain.cache.rebind import RebindProposal
from brain.settings import BrainSettings, CacheSettings


class ChangedEditorRelay(FakeRelay):
    def observe(self):
        observation = super().observe()
        if self.mode == "editor":
            observation.elements.append(element("e900", "Pane", name="New wrapper", parent="e0"))
            for index, ref in enumerate(self.field_refs.values()):
                item = next(e for e in observation.elements if e.ref == ref)
                item.parent_ref = "e900"
                if index >= 5:
                    item.name = f"Changed input {index + 1}"
        return observation


def settings(path, mode="rebind"):
    return BrainSettings(cache=CacheSettings(enabled=True, path=path, matching_mode=mode))


def test_full_graph_rebinds_six_fields_with_one_measured_call(tmp_path, record):
    path = tmp_path / "cache.json"
    cold, _, _, cold_model = run_brain(
        tmp_path / "cold", record, success_steps(record), settings=settings(path)
    )
    assert cold.status == "success"
    relay = ChangedEditorRelay(record)

    def proposal(prompt):
        data = json.loads(prompt.split("MAPPING_DATA=", 1)[1])
        return {
            "observation_id": data["observation_id"],
            "bindings": [
                {"field": entry["field"], "ref": relay.field_refs[entry["field"]]}
                for entry in data["learned_fields"]
            ],
        }

    steps = [step for step in success_steps(record) if step["intent"] != "fill"]
    steps.insert(4, proposal)
    warm, brain, _, model = run_brain(
        tmp_path / "warm", record, steps, settings=settings(path), relay=relay
    )
    assert warm.status == "success", warm
    assert relay.inputs == values_for(record)
    assert (
        len(cold_model.prompts) - len(model.prompts) == 10
    )  # 11 cached inputs minus one measured mapping.
    assert brain.cache_rebind_calls == 1
    hits = [e for e in brain.log.read_all() if e.kind == "cache_hit"]
    assert sum(e.payload["match_mode"] == "label_anchor" for e in hits) == 5
    assert sum(e.payload["match_mode"] == "model_rebind" for e in hits) == 6
    assert len([e for e in brain.log.read_all() if e.kind == "model_call"]) == len(model.prompts)
    assert len(SemanticCache(path).entries()) == 22


def test_empty_or_invalid_proposals_fall_back_without_unmeasured_retry(tmp_path, record):
    path = tmp_path / "cache.json"
    cold, _, _, _ = run_brain(
        tmp_path / "cold", record, success_steps(record), settings=settings(path)
    )
    assert cold.status == "success"
    cold_cache = path.read_bytes()

    for variant in ("empty", "unknown-ref", "invalid-schema"):
        path.write_bytes(cold_cache)
        relay = ChangedEditorRelay(record)
        changed = set(list(relay.field_refs)[5:])

        def proposal(prompt, variant=variant, changed=changed):
            data = json.loads(prompt.split("MAPPING_DATA=", 1)[1])
            if variant == "invalid-schema":
                return {"unexpected": "invalid response"}
            return {
                "observation_id": data["observation_id"],
                "bindings": (
                    [{"field": next(iter(changed)), "ref": "e9999"}]
                    if variant == "unknown-ref"
                    else []
                ),
            }

        steps = [
            step
            for step in success_steps(record)
            if step["intent"] != "fill" or step["action"]["source"] in changed
        ]
        steps.insert(4, proposal)
        result, brain, _, _ = run_brain(
            tmp_path / f"warm-{variant}", record, steps, settings=settings(path), relay=relay
        )
        assert result.status == "success", (variant, result)
        assert brain.cache_rebind_calls == 1
        assert relay.inputs == values_for(record)
        assert all(
            event.payload["match_mode"] == "label_anchor"
            for event in brain.log.read_all()
            if event.kind == "cache_hit"
        )


def test_unknown_cost_in_rebind_call_stops_before_changed_label_inputs(
    tmp_path, record, monkeypatch
):
    path = tmp_path / "cache.json"
    cold, _, _, _ = run_brain(
        tmp_path / "cold", record, success_steps(record), settings=settings(path)
    )
    assert cold.status == "success"
    original = path.read_bytes()
    generate = ScriptedModel._generate

    def failing(self, prompt, schema, *, images=None):
        if schema is RebindProposal:
            raise OSError("simulated provider failure")
        return generate(self, prompt, schema, images=images)

    monkeypatch.setattr(ScriptedModel, "_generate", failing)
    relay = ChangedEditorRelay(record)
    steps = [step for step in success_steps(record) if step["intent"] != "fill"]
    result, brain, _, _ = run_brain(
        tmp_path / "warm", record, steps, settings=settings(path), relay=relay
    )
    assert result.status == "stopped" and result.reason == "unknown_model_call_cost"
    assert brain.cache_rebind_calls == 1
    assert all(relay.inputs[field] == "" for field in list(relay.field_refs)[5:])
    assert path.read_bytes() == original
