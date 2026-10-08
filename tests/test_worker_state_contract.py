"""The worker graph exposes four responsibilities through one state contract."""

from brain.graph.state import copy_worker_state, create_worker_state
from brain.graph.workflow import build_worker_graph


class WorkerStub:
    def observation_node(self, state):
        return state

    def decision_node(self, state):
        return state

    def execution_node(self, state):
        return state

    def review_node(self, state):
        return state


def test_worker_graph_and_state_have_four_explicit_responsibilities():
    state = create_worker_state()
    copied = copy_worker_state(state)
    copied["decision"]["task_plan"].append("step")

    assert set(state) == {"observation", "decision", "execution", "review", "lifecycle"}
    assert state["decision"]["task_plan"] == []
    assert state["execution"] == {"pending": None, "authorized": False}
    assert state["review"] == {
        "current": None,
        "decision_pending": False,
        "execution_pending": False,
    }
    assert {
        name
        for name in build_worker_graph(WorkerStub()).get_graph().nodes
        if not name.startswith("__")
    } == {"observation", "decision", "execution", "review"}


def test_nested_handoff_and_returned_node_state_are_isolated():
    from brain.graph import Brain
    from brain.graph.execution import ActionPlan

    state = create_worker_state()
    state["decision"]["action_plan"] = ActionPlan((), audit={"fields": ["original"]})
    state["observation"]["rolling_turns"] = [{"effects": ["before"]}]
    brain = Brain.__new__(Brain)
    brain._adopt_worker_state(state)
    brain.action_plan.audit["fields"].append("internal")
    assert state["decision"]["action_plan"].audit == {"fields": ["original"]}
    output = brain._complete_responsibility({"next": "observe"})
    output["observation"]["rolling_turns"][0]["effects"].append("external")
    output["lifecycle"]["steps"] = 100
    assert brain.steps == 0
    assert brain._worker_state["observation"]["rolling_turns"] == [{"effects": ["before"]}]
