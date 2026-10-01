"""Builds the graph (PLAN.md §2) and opens it with the SQLite checkpointer.

Until Phases 5 and 6 land, `build` is a stub that returns a canned ResultDigest.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

import r2r.contracts as contracts
from r2r.graph import nodes
from r2r.graph.deps import RunDeps
from r2r.graph.routing import (
    GateKind,
    next_milestone,
    route_after_coordinator,
    route_after_gate,
)
from r2r.graph.state import PipelineState

GATES: dict[GateKind, Callable] = {
    "roadmap": nodes.roadmap_gate,
    "escalation": nodes.escalation_gate,
    "result": nodes.result_gate,
}


def _after_long_plan(state: PipelineState) -> str:
    if next_milestone(state["roadmap"]) is None:
        return "finish"
    if state.get("approved_roadmap") != state["roadmap"]:
        return "roadmap_gate"
    return "short_plan"


def _after_coordinate(state: PipelineState, max_rounds: int) -> str:
    return route_after_coordinator(state["verdict"], state.get("revise_rounds", 0), max_rounds)


def _after_gate(state: PipelineState, gate: GateKind) -> str:
    return route_after_gate(gate, state["human_decision"])


def build_graph(deps: RunDeps) -> StateGraph:
    g = StateGraph(PipelineState)
    work = {
        "retrieve": nodes.retrieve,
        "long_plan": nodes.long_plan,
        "short_plan": nodes.short_plan,
        "coordinate": nodes.coordinate,
        "build": nodes.build_stub,
        "finish": nodes.finish,
    }
    for name, fn in work.items():
        g.add_node(name, partial(fn, deps=deps))
    for kind, fn in GATES.items():
        g.add_node(f"{kind}_gate", partial(fn, deps=deps))

    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "long_plan")
    g.add_conditional_edges("long_plan", _after_long_plan, ["finish", "roadmap_gate", "short_plan"])
    g.add_edge("short_plan", "coordinate")
    max_rounds = deps.settings.budgets.coordinator_max_revise_rounds
    g.add_conditional_edges(
        "coordinate",
        partial(_after_coordinate, max_rounds=max_rounds),
        ["build", "short_plan", "long_plan", "escalation_gate"],
    )
    g.add_edge("build", "result_gate")
    targets = {
        "roadmap": ["short_plan", "long_plan", END],
        "escalation": ["build", "long_plan", END],
        "result": ["long_plan", END],
    }
    for kind in GATES:
        g.add_conditional_edges(f"{kind}_gate", partial(_after_gate, gate=kind), targets[kind])
    g.add_edge("finish", END)
    return g


def serializer() -> JsonPlusSerializer:
    """Checkpoint serializer that may only rebuild r2r contract types."""
    allowed = [
        (obj.__module__, obj.__name__)
        for obj in vars(contracts).values()
        if isinstance(obj, type) and issubclass(obj, BaseModel)
    ]
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)


@contextmanager
def open_graph(deps: RunDeps) -> Iterator[CompiledStateGraph]:
    conn = sqlite3.connect(str(deps.paths.checkpoints), check_same_thread=False)
    try:
        saver = SqliteSaver(conn, serde=serializer())
        yield build_graph(deps).compile(checkpointer=saver)
    finally:
        conn.close()
