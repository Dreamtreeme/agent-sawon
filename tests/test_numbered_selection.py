"""Number-only choices retain the existing action and evidence execution path."""

import json

import pytest
from test_brain import element
from test_brain import record as record
from test_screen_batch import ChangingPixelsRelay, make_brain

from brain.graph.execution import GuardStop
from brain.graph.notebook import NumberSelection, Option, _ask, _proofs
from brain.graph.screen_batch import LoginScreenPlan
from brain.visual.scene import SceneObservation


class NamedPerception:
    def __init__(self, relay):
        self.relay = relay
        self.action_counts = []

    def observe(self, wire):
        self.action_counts.append(len(self.relay.actions))
        fields = {ref: field for field, ref in self.relay.field_refs.items()}
        items = [
            e.model_copy(update={"name": fields[e.ref]}) if e.ref in fields else e
            for e in self.relay.items
        ]
        if self.relay.saved:
            items.append(element("e90", "Text", name="Saved. Query records to verify."))
        return SceneObservation(wire=wire, screen_kind=self.relay.mode, elements=items)


def make_numbered(tmp_path, record, responses, *, mode="query", duplicate=False):
    relay = ChangingPixelsRelay(record, duplicate=duplicate)
    relay.mode = mode
    brain, _, model = make_brain(tmp_path, record, relay, responses)
    brain.settings = brain.settings.model_copy(update={"numbered_selection": True})
    brain.visual_perception = NamedPerception(relay)
    return brain, relay, model


def choose(*labels):
    def respond(prompt):
        payload = json.loads(prompt.split("\n", 1)[1])
        assert len(labels) == len(payload["steps"])
        result = []
        for wanted, step in zip(labels, payload["steps"], strict=True):
            matches = [
                int(k)
                for k, label in payload["options"].items()
                if int(k) in step["labels"] and label.startswith(wanted)
            ]
            assert len(matches) == 1, (wanted, payload)
            result.append(matches[0])
        return {"labels": result}

    return respond


def test_numbered_workflow_reuses_batch_and_final_evidence(tmp_path, record):
    fields = sorted(record.fields.model_dump())
    fields.remove("packages")
    fields = sorted([*fields, "packages.count", "packages.unit"])
    plan = {
        "duplicate_query": "Check duplication",
        "enter": "Fill and save",
        "save_result": "Inspect save result",
        "final_query": "Query and verify",
    }
    responses = [
        plan,
        choose("query_input: Query identifier"),
        choose("search: Run query"),
        choose("duplicate_clear:"),
        choose("navigate: New object"),
        choose(*(f + " (" for f in fields), "Unavailable"),
        choose("save: Persist object"),
        choose("ready_for_query: observed message"),
        choose("Enter task identifier:"),
        choose("search: Run query"),
        choose("Complete:"),
    ]
    brain, relay, model = make_numbered(tmp_path, record, responses)
    result = brain.run()
    assert result.status == "success", result
    assert relay.saved and set(brain.verified) == set(brain.values)
    counts = brain.visual_perception.action_counts
    assert not any(4 <= count <= 14 for count in counts)
    memo = json.loads((tmp_path / "task-notebook.json").read_text())
    assert memo["result"]["status"] == "success"
    assert memo["stage_goals"] == plan
    assert memo["remaining_fields"] == []
    for prompt in model.prompts[1:]:
        assert "TRUSTED_TASK" not in prompt and "bbox" not in prompt
        assert brain.values["hbl_no"] not in prompt
        assert "e20" not in prompt
    assert len(json.dumps(NumberSelection.model_json_schema())) < 350


@pytest.mark.parametrize("labels", [[2], [1, 1], [-1]])
def test_selection_rejects_unoffered_labels_before_actions(tmp_path, record, labels):
    brain, relay, _ = make_numbered(tmp_path, record, [{"labels": labels}])
    brain._observe()
    with pytest.raises(GuardStop, match="numbered_choice_not_offered"):
        _ask(brain, [("Choose", [Option("Visible button", "e2")])])
    assert not relay.actions


def test_selection_catalog_is_shared_but_membership_is_per_goal(tmp_path, record):
    brain, _, model = make_numbered(tmp_path, record, [{"labels": [1, 2]}])
    brain._observe()
    assert _ask(
        brain, [("A", [Option("Same", "e1")]), ("B", [Option("Same", "e1"), Option("Other", "e2")])]
    ) == ["e1", "e2"]
    payload = json.loads(model.prompts[0].split("\n", 1)[1])
    assert len(payload["options"]) == 3  # Includes zero, no repeated catalog.
    assert payload["steps"][0]["labels"] == [0, 1]


def test_choices_expire_when_current_observation_changes(tmp_path, record):
    def change_scene(prompt):
        brain._observe()
        return {"labels": [1]}

    brain, relay, _ = make_numbered(tmp_path, record, [change_scene])
    brain._observe()
    with pytest.raises(GuardStop, match="numbered_choice_expired"):
        _ask(brain, [("Choose", [Option("Visible button", "e2")])])
    assert not relay.actions


@pytest.mark.parametrize("duplicate", [False, True])
def test_duplicate_choices_use_current_table_evidence(tmp_path, record, duplicate):
    brain, relay, _ = make_numbered(tmp_path, record, [], duplicate=duplicate)
    relay.query = brain.values["hbl_no"]
    brain._observe()
    brain.query_submitted = brain.query_value_verified = True
    options = _proofs(brain)
    assert [o.value.operation for o in options] == [
        "duplicate_found" if duplicate else "duplicate_clear"
    ]


def test_login_numbers_compile_to_one_plan_without_credentials(tmp_path, record):
    brain, _, model = make_numbered(
        tmp_path, record, [choose("Password", "Account", "Sign in")], mode="login"
    )
    brain.credentials = {"credential.username": "private-user", "credential.password": "private-pw"}
    brain._observe()
    brain.observation = brain.observation.model_copy(
        update={
            "elements": [
                element("e101", "Edit", name="Account"),
                element("e102", "Edit", name="Password"),
                element("e103", "Button", name="Sign in"),
            ]
        }
    )
    plan = brain._selection_call("DO NOT SEND THIS FULL PROMPT", LoginScreenPlan)
    assert {x.source: x.ref for x in plan.fields} == {
        "credential.username": "e101",
        "credential.password": "e102",
    }
    assert plan.login_ref == "e103"
    assert len(model.prompts) == 1
    assert all(
        x not in model.prompts[0] for x in ("private-user", "private-pw", "e101", "FULL PROMPT")
    )


def test_perception_receives_no_global_task_or_plan(tmp_path, record):
    from brain.visual.scene import SemanticDetection

    brain, _, model = make_numbered(tmp_path, record, [{"screen_kind": "query", "controls": []}])
    brain.goal = "private global task"
    brain.plan = ["private long plan"]
    brain._detect_visual("CURRENT MARKERS", SemanticDetection, None)
    assert len(model.prompts) == 1
    assert "CURRENT MARKERS" in model.prompts[0]
    assert all(
        x not in model.prompts[0] for x in (brain.goal, brain.plan[0], brain.values["hbl_no"])
    )
