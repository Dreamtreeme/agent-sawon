"""Observation → Decision → Execution → Review worker graph."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

from .state import WorkerState

if TYPE_CHECKING:
    from .engine import Brain


def route_after_responsibility(state: WorkerState) -> str:
    return state["lifecycle"]["route"]


def build_worker_graph(brain: Brain):
    """Compile the four responsibility nodes around the shared state contract."""

    graph = StateGraph(WorkerState)
    nodes = {
        "observation": brain.observation_node,
        "decision": brain.decision_node,
        "execution": brain.execution_node,
        "review": brain.review_node,
    }
    destinations = {**{name: name for name in nodes}, "finish": END}
    for name, node in nodes.items():
        graph.add_node(name, node)
        graph.add_conditional_edges(name, route_after_responsibility, destinations)
    graph.add_edge(START, "observation")
    return graph.compile()


__all__ = ["build_worker_graph", "route_after_responsibility"]
