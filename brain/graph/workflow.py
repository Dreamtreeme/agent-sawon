"""Observation → Decision → Review → Execution worker graph."""

from __future__ import annotations

from typing import Protocol

from langgraph.graph import END, START, StateGraph

from .state import WorkerState


class WorkerNodes(Protocol):
    """Only the four state transformations are needed to construct the graph."""

    def observation_node(self, state: WorkerState) -> WorkerState: ...
    def decision_node(self, state: WorkerState) -> WorkerState: ...
    def execution_node(self, state: WorkerState) -> WorkerState: ...
    def review_node(self, state: WorkerState) -> WorkerState: ...


def route_after_responsibility(state: WorkerState) -> str:
    return state["lifecycle"]["route"]


def build_worker_graph(brain: WorkerNodes):
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
