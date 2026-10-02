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
