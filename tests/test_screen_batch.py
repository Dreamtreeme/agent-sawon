"""Screen plans fill several controls before one joint reread."""

import hashlib
import json

import pytest
from test_brain import ScriptedModel, success_steps, values_for
from test_brain import record as record
from test_visual_workflow import SyntheticPerception, VisualFixture

from brain.graph.engine import Brain
from brain.runtime.storage import RunLog
from brain.settings import BrainSettings


class ChangingPixelsRelay(VisualFixture):
    def observe(self):
        wire = super().observe()
        digest = hashlib.sha1(json.dumps([self.mode, self.query, self.inputs, self.saved]).encode())
        self.current = wire.model_copy(update={"screen_hash": digest.hexdigest()})
        return self.current


class CountPerception(SyntheticPerception):
    def __init__(self, relay):
        super().__init__(relay)
        self.action_counts = []

    def observe(self, wire):
        self.action_counts.append(len(self.relay.actions))
        return super().observe(wire)


def screen_plan(record):
    return {
        "fields": [{"source": f, "ref": f"e{i + 20}"} for i, f in enumerate(values_for(record))],
        "next_decision": None,
    }


def make_brain(tmp_path, record, relay, responses):
    log = RunLog(tmp_path, case_id=record.case_id, agent_id="screen-test")
    model = ScriptedModel(log, responses)
    brain = Brain(
        record=record,
        goal="Save for approval",
        relay=relay,
        model=model,
        settings=BrainSettings(perception_mode="visual_only", visual_screen_fill=True),
        case_dir=tmp_path,
        run_log=log,
        visual_ocr=object(),
    )
    perception = CountPerception(relay)
    brain.visual_perception = perception
    return brain, perception, model


def test_complete_workflow_fills_all_fields_before_one_reread(tmp_path, record):
    ordinary = success_steps(record)
    batch = screen_plan(record)
    responses = (
        ordinary[:3]
        + [{"fields": [], "next_decision": {"decision": ordinary[3]}}, batch]
        + ordinary[4 + len(batch["fields"]) :]
    )
    relay = ChangingPixelsRelay(record)
    brain, perception, model = make_brain(tmp_path, record, relay, responses)
    result = brain.run()
    assert result.status == "success", result
    assert relay.saved and set(brain.verified) == set(brain.values)
    # Three pre-entry actions, then eleven fields and one final focus move.
    assert not any(4 <= count <= 14 for count in perception.action_counts)
    assert perception.action_counts.count(15) == 1
    assert len(model.prompts) == 10  # Previously 20 including the initial plan.
    events = brain.log.read_all()
    checks = [e for e in events if e.payload.get("check") == "screen_fill_values"]
    assert len(checks) == 1 and len(checks[0].payload["matched"]) == 11
    assert not checks[0].payload["mismatched"]
    renewals = [e for e in events if e.payload.get("check") == "visual_planned_capture_renewal"]
    assert len(renewals) == 11  # Remaining ten inputs and the final focus move.
    assert all(e.payload["screen_batch"] and not e.payload["same_content"] for e in renewals)


def test_wrong_field_is_not_verified_and_only_that_field_is_replanned(tmp_path, record):
    class CorruptOnce(ChangingPixelsRelay):
        corrupt_once = True

        def act(self, action):
            result = super().act(action)
            if self.corrupt_once and self.inputs["mbl_no"]:
                self.inputs["mbl_no"] = "WRONG"
                self.corrupt_once = False
            return result

    relay = CorruptOnce(record)
    relay.mode = "editor"
    repair = {"fields": [{"source": "mbl_no", "ref": "e21"}], "next_decision": None}
    brain, perception, _ = make_brain(tmp_path, record, relay, [screen_plan(record), repair])
    brain.phase, brain.duplicate_clear = "enter", True
    brain._observe()
    assert brain._select_node({}) == {"next": "review"}
    assert set(brain.verified) == set(brain.values) - {"mbl_no"}
    assert brain._select_node({}) == {"next": "review"}
    assert set(brain.verified) == set(brain.values)
    assert perception.action_counts == [0, 12, 14]


@pytest.mark.parametrize("failure", ["button", "duplicate_ref", "window_change", "move"])
def test_invalid_plan_and_mid_batch_geometry_change_stop_remaining_inputs(
    tmp_path, record, failure
):
    class DriftingRelay(ChangingPixelsRelay):
        def observe(self):
            wire = super().observe()
            if self.actions and failure == "window_change":
                wire = wire.model_copy(
                    update={
                        "active_window": wire.active_window.model_copy(
                            update={"title": "Different dialog"}
                        )
                    }
                )
            if self.actions and failure == "move":
                geometry = wire.screenshot.geometry.model_copy(update={"origin": (200, 200)})
                wire = wire.model_copy(
                    update={"screenshot": wire.screenshot.model_copy(update={"geometry": geometry})}
                )
            self.current = wire
            return wire

    relay = DriftingRelay(record)
    relay.mode = "editor"
    plan = screen_plan(record)
    if failure == "button":
        plan["fields"][-1]["ref"] = "e7"
    if failure == "duplicate_ref":
        plan["fields"][-1]["ref"] = "e20"
    brain, _, _ = make_brain(tmp_path, record, relay, [plan])
    brain.phase, brain.duplicate_clear = "enter", True
    brain._observe()
    brain._select_node({})
    assert len(relay.actions) == (1 if failure in {"window_change", "move"} else 0)
    assert not brain.verified and not relay.saved
