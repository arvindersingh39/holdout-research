"""Builds the graph (PLAN.md §2) and opens it with the SQLite checkpointer.

The optimizer loop (Phase 6) slots in between `code` and `digest`.
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
    needs_test_review,
    next_milestone,
    route_after_code,
    route_after_coordinator,
    route_after_design,
    route_after_gate,
    route_after_optimize,
)
from r2r.graph.state import PipelineState

GATES: dict[GateKind, Callable] = {
    "roadmap": nodes.roadmap_gate,
    "escalation": nodes.escalation_gate,
    "tests": nodes.tests_gate,
    "design": nodes.design_gate,
    "coder": nodes.coder_gate,
    "tamper": nodes.tamper_gate,
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


def _after_design(state: PipelineState, review_tests: str) -> str:
    review = needs_test_review(review_tests, state["roadmap"])
    return route_after_design(
        state.get("redgreen_ok", False), state.get("design_attempts", 0), review
    )


def _after_code(state: PipelineState, max_sessions: int) -> str:
    result = state.get("test_result")
    return route_after_code(
        bool(state.get("tamper")),
        bool(result and result.passed),
        state.get("coder_sessions", 0),
        max_sessions,
    )


def _after_opt_baseline(state: PipelineState) -> str:
    return "digest" if state.get("opt_stop_reason") else "optimize"


def _after_optimize(state: PipelineState) -> str:
    return route_after_optimize(bool(state.get("tamper")), state.get("opt_stop_reason"))


def _after_gate(state: PipelineState, gate: GateKind) -> str:
    return route_after_gate(gate, state["human_decision"])


def build_graph(deps: RunDeps) -> StateGraph:
    g = StateGraph(PipelineState)
    work = {
        "retrieve": nodes.retrieve,
        "long_plan": nodes.long_plan,
        "short_plan": nodes.short_plan,
        "coordinate": nodes.coordinate,
        "design": nodes.design,
        "freeze": nodes.freeze_tests,
        "code": nodes.code,
        "opt_baseline": nodes.opt_baseline,
        "optimize": nodes.optimize,
        "digest": nodes.digest,
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
    b = deps.settings.budgets
    g.add_conditional_edges(
        "coordinate",
        partial(_after_coordinate, max_rounds=b.coordinator_max_revise_rounds),
        ["design", "short_plan", "long_plan", "escalation_gate"],
    )
    g.add_conditional_edges(
        "design",
        partial(_after_design, review_tests=b.gates.review_tests),
        ["tests_gate", "freeze", "design", "design_gate"],
    )
    g.add_edge("freeze", "code")
    g.add_conditional_edges(
        "code",
        partial(_after_code, max_sessions=b.coder_max_sessions),
        ["tamper_gate", "opt_baseline", "code", "coder_gate"],
    )
    g.add_conditional_edges("opt_baseline", _after_opt_baseline, ["digest", "optimize"])
    g.add_conditional_edges("optimize", _after_optimize, ["tamper_gate", "digest", "optimize"])
    g.add_edge("digest", "result_gate")
    targets = {
        "roadmap": ["short_plan", "long_plan", END],
        "escalation": ["design", "long_plan", END],
        "tests": ["freeze", "design", END],
        "design": ["freeze", "long_plan", END],
        "coder": ["digest", "long_plan", END],
        "tamper": ["code", "long_plan", END],
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
