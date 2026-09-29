"""A lost query-input proof reopens entry before search, never skips its result."""

import pytest
from test_brain import FakeRelay, decision, element, success_steps
from test_brain import record as record
from test_policy_workflow import make_run, schema_accepts, schema_rejects, task_state


class ClearedQueryRelay(FakeRelay):
    def __init__(self, record, *, after_save):
        super().__init__(record)
        self.after_save = after_save
        self.armed = False
        self.matching_reads = 0
        self.cleared = False

    def act(self, action):
        result = super().act(action)
        if (
            action.root.type == "set_text"
            and action.root.target.ref == "e1"
            and self.saved == self.after_save
        ):
            self.armed = True
        return result

    def observe(self):
        if (
            self.armed
            and self.mode == "query"
            and self.query == self.values["hbl_no"]
            and self.saved == self.after_save
            and not self.cleared
        ):
            self.matching_reads += 1
            if self.matching_reads == 2:
                self.query = ""
                self.cleared = True
        return super().observe()


@pytest.mark.parametrize("after_save", [False, True])
def test_cleared_query_reopens_input_contract_then_full_workflow_succeeds(
    tmp_path, record, after_save
):
    relay = ClearedQueryRelay(record, after_save=after_save)
    steps = success_steps(record)

    def repair_query(prompt):
        assert relay.cleared
        assert not brain.query_value_verified
        assert not brain.query_submitted
        assert brain.query_descriptor is None
        assert brain.query_window_handle is None
        policy = task_state(prompt)["execution_policy"]
        assert policy["current_step"] == "query_input"
        schema_accepts(model.selection_schemas[-1], steps[0])
        schema_rejects(model.selection_schemas[-1], steps[1])
        return steps[0]

    insertion = len(steps) - 2 if after_save else 1
    brain, model = make_run(
        tmp_path, record, relay, [*steps[:insertion], repair_query, *steps[insertion:]]
    )
    result = brain.run()
    assert result.status == "success", result
    assert (
        sum(
            action.root.type == "set_text" and action.root.target.ref == "e1"
            for action in relay.actions
        )
        == 3
    )
    invalidations = [
        event.payload
        for event in brain.log.read_all()
        if event.kind == "verify" and event.payload.get("check") == "query_input_invalidated"
    ]
    assert len(invalidations) == 1


class QueryNavigationRelay(FakeRelay):
    def observe(self):
        observation = super().observe()
        observation.elements.append(element("e90", "Button", name="Switch view", parent="e0"))
        return observation

    def act(self, action):
        if action.root.type == "click" and action.root.target.ref == "e90":
            self.mode = "menu" if self.mode == "query" else "query"
            if self.mode == "query":
                self.query = ""
        return super().act(action)


def test_leaving_query_controls_reopens_input_on_return(tmp_path, record):
    relay = QueryNavigationRelay(record)
    steps = success_steps(record)

    def enter_returned_query(prompt):
        assert not brain.query_value_verified
        assert task_state(prompt)["execution_policy"]["current_step"] == "query_input"
        schema_accepts(model.selection_schemas[-1], steps[0])
        return steps[0]

    brain, model = make_run(
        tmp_path,
        record,
        relay,
        [
            steps[0],
            decision(intent="navigate", action={"type": "click", "ref": "e90"}),
            decision(kind="menu", intent="navigate", action={"type": "click", "ref": "e90"}),
            enter_returned_query,
            *steps[1:],
        ],
    )
    result = brain.run()
    assert result.status == "success", result
    assert relay.saved


class SubmittedQueryChangedRelay(FakeRelay):
    def act(self, action):
        result = super().act(action)
        if action.root.type == "click" and action.root.target.ref == "e2":
            self.query = "Different synthetic query"
        return result


def test_submitted_query_change_still_stops_at_mandatory_duplicate_checkpoint(tmp_path, record):
    relay = SubmittedQueryChangedRelay(record)
    brain, _ = make_run(tmp_path, record, relay, success_steps(record))
    result = brain.run()
    assert result.status == "stopped"
    assert result.reason == "query_value_changed"
    assert brain.query_submitted
    assert not brain.duplicate_clear
    assert not relay.saved
    assert len(relay.actions) == 2
