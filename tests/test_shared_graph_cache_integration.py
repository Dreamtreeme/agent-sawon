"""Semantic cache runs through the complete graph and ordinary value guards."""

from copy import deepcopy

from test_brain import FakeRelay, run_brain, success_steps, values_for
from test_brain import record as record

from brain.cache import SemanticCache
from brain.settings import BrainSettings, CacheSettings


def cache_settings(path):
    return BrainSettings(cache=CacheSettings(enabled=True, path=path, max_retries=1))


def current_refs(step, relay):
    def resolve(_prompt):
        item = deepcopy(step)
        if (item.get("action") or {}).get("ref"):
            ref = item["action"]["ref"]
            item["action"]["ref"] = f"e{int(ref[1:]) + relay.ref_shift}"
        for key in ("grid_ref", "row_ref"):
            if item.get(key):
                item[key] = f"e{int(item[key][1:]) + relay.ref_shift}"
        for evidence in item.get("evidence", []):
            evidence["ref"] = f"e{int(evidence['ref'][1:]) + relay.ref_shift}"
        return item

    return resolve


class RenumberingRelay(FakeRelay):
    def __init__(self, record):
        super().__init__(record)
        self.change_refs = True

    def act(self, action):
        # Base fixture rebases text targets only; cover observed button targets
        # here too so every action resolves the newest observation's namespace.
        if action.root.type == "click":
            action = action.model_copy(deep=True)
            action.root.target.ref = f"e{int(action.root.target.ref[1:]) - self.ref_shift}"
        return super().act(action)


def test_cold_promotes_warm_reuses_with_new_values_and_refs(tmp_path, record):
    cache_path = tmp_path / "semantic.json"
    settings = cache_settings(cache_path)
    cold, cold_brain, _, cold_model = run_brain(
        tmp_path / "cold", record, success_steps(record), settings=settings
    )
    assert cold.status == "success", cold
    assert len(SemanticCache(cache_path).entries()) == 11

    newer = record.model_copy(deep=True)
    newer.case_id = "warm-fixture"
    newer.fields.hbl_no.value = "SECOND-HBL-IDENTIFIER"
    newer.fields.shipper.value = "New supplier"
    newer.fields.packages.value.count = 34
    newer.fields.gross_weight_kg.value = 777.0
    relay = RenumberingRelay(newer)
    remaining = [step for step in success_steps(newer) if step["intent"] != "fill"]
    warm, warm_brain, _, warm_model = run_brain(
        tmp_path / "warm",
        newer,
        [current_refs(step, relay) for step in remaining],
        settings=settings,
        relay=relay,
    )
    assert warm.status == "success", warm
    assert len(cold_model.prompts) - len(warm_model.prompts) == 11
    assert relay.inputs == values_for(newer)
    events = warm_brain.log.read_all()
    assert len([event for event in events if event.kind == "cache_hit"]) == 11
    assert (
        len(
            [
                event
                for event in events
                if event.kind == "verify" and event.payload.get("check") == "input_value_matches"
            ]
        )
        == 13
    )
    assert warm.steps == cold.steps
    assert all(entry.success_count == 2 for entry in SemanticCache(cache_path).entries())
    assert cold_brain.result.status == "success"


def test_corrupt_cache_preserved_while_model_finishes_task(tmp_path, record):
    cache_path = tmp_path / "semantic.json"
    original = b'{"schema_version":"unsupported"}'
    cache_path.write_bytes(original)
    result, brain, relay, _ = run_brain(
        tmp_path / "run", record, success_steps(record), settings=cache_settings(cache_path)
    )
    assert result.status == "success", result
    assert cache_path.read_bytes() == original
    assert relay.inputs == values_for(record)
    assert any(
        event.kind == "cache_miss" and event.payload.get("reason") == "cache_integrity_error"
        for event in brain.log.read_all()
    )
