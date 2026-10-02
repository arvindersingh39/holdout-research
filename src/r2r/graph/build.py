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
from r2r.llm.gateway import BudgetExceeded

GATES: dict[GateKind, Callable] = {
    "roadmap": nodes.roadmap_gate,
    "escalation": nodes.escalation_gate,
    "tests": nodes.tests_gate,
    "design": nodes.design_gate,
    "coder": nodes.coder_gate,
    "tamper": nodes.tamper_gate,
    "budget": nodes.budget_gate,
    "dispute": nodes.dispute_gate,
    "result": nodes.result_gate,
    "publication": nodes.publication_gate,
}

# Nodes that call an LLM: a BudgetExceeded there routes to the budget gate (D46). The optimizer
# loop handles its own budget stop (D40).
GUARDED = (
    "retrieve",
    "long_plan",
    "short_plan",
    "coordinate",
    "scout",
    "design",
    "code",
    "revise_test",
    "digest",
    "publish",
    "oversee_plan",
    "oversee_spec",
    "oversee_design",
    "oversee_design_gate",
    "oversee_code",
    "oversee_result",
)

# Phase 7c: where Python routes through the overseer, and where each hook continues.
HOOKS = {
    "roadmap_gate": "oversee_plan",
    "scout": "oversee_spec",
    "design": "oversee_design",
    "design_gate": "oversee_design_gate",  # the owner decides after two failed designs
    "code": "oversee_code",
    "result_gate": "oversee_result",
}


def _guard(name: str, fn: Callable, deps: RunDeps) -> Callable:
    def node(state: PipelineState) -> dict:
        try:
            return fn(state, deps=deps)
        except BudgetExceeded as e:
            return {"budget_blocked": name, "budget_message": str(e)}

    return node


def _guarded_route(name: str, route: Callable[[PipelineState], str]) -> Callable:
    def r(state: PipelineState) -> str:
        return "budget_gate" if state.get("budget_blocked") == name else route(state)

    return r


def _after_budget_gate(state: PipelineState) -> str:
    if state["human_decision"].action == "stop":
        return END
    return state.get("budget_retry") or "long_plan"


def _via_overseer(route: Callable[[PipelineState], str], src: str, lim) -> Callable:
    """Send `src`'s next step through its overseer hook when enabled and under the call cap.
    Only the hook's own edge returns to the original target: the overseer never routes."""
    hooked = {
        "long_plan": {"roadmap_gate"},
        "coordinate": {"scout"},
        "design": {"design", "design_gate"},
        "code": {"code"},
        "digest": {"result_gate"},
    }[src]

    def r(state: PipelineState) -> str:
        target = route(state)
        on = lim.enabled and state.get("oversee_calls", 0) < lim.max_calls_milestone
        return HOOKS[target] if on and target in hooked else target

    return r


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
        disputed=bool(state.get("dispute")),
    )


def _after_revise(state: PipelineState) -> str:
    ok = state.get("dispute_file") is None and not state.get("coder_message")
    return "code" if ok else "coder_gate"


def _after_opt_baseline(state: PipelineState) -> str:
    return "digest" if state.get("opt_stop_reason") else "optimize"


def _after_optimize(state: PipelineState) -> str:
    return route_after_optimize(bool(state.get("tamper")), state.get("opt_stop_reason"))


def _after_publication(state: PipelineState) -> str:
    """One revision pass with the owner's note (Phase 8); then approve or stop."""
    d = state["human_decision"]
    if d.action == "redirect" and state.get("publish_revisions", 0) <= 1:
        return "publish"
    return END


def _after_gate(state: PipelineState, gate: GateKind) -> str:
    return route_after_gate(gate, state["human_decision"])


def build_graph(deps: RunDeps) -> StateGraph:
    g = StateGraph(PipelineState)
    work = {
        "retrieve": nodes.retrieve,
        "long_plan": nodes.long_plan,
        "short_plan": nodes.short_plan,
        "coordinate": nodes.coordinate,
        "scout": nodes.scout,
        "design": nodes.design,
        "revise_test": nodes.revise_test,
        "freeze": nodes.freeze_tests,
        "code": nodes.code,
        "opt_baseline": nodes.opt_baseline,
        "optimize": nodes.optimize,
        "digest": nodes.digest,
        **{
            hook: partial(nodes.oversee, point=hook.removeprefix("oversee_"))  # e.g. design_gate
            for hook in HOOKS.values()
        },
        "finish": nodes.finish,
        "publish": nodes.publish,
    }
    for name, fn in work.items():
        node = _guard(name, fn, deps) if name in GUARDED else partial(fn, deps=deps)
        g.add_node(name, node)
    for kind, fn in GATES.items():
        g.add_node(f"{kind}_gate", partial(fn, deps=deps))

    def edge(src: str, route: Callable[[PipelineState], str], targets: list[str]) -> None:
        if src in GUARDED:
            g.add_conditional_edges(src, _guarded_route(src, route), [*targets, "budget_gate"])
        else:
            g.add_conditional_edges(src, route, targets)

    b = deps.settings.budgets
    ov = b.overseer
    for target, hook in HOOKS.items():
        edge(hook, lambda s, t=target: t, [target])
    g.add_edge(START, "retrieve")
    edge("retrieve", lambda s: "long_plan", ["long_plan"])
    edge(
        "long_plan",
        _via_overseer(_after_long_plan, "long_plan", ov),
        ["finish", "roadmap_gate", "short_plan", "oversee_plan"],
    )
    edge("short_plan", lambda s: "coordinate", ["coordinate"])
    edge(
        "coordinate",
        _via_overseer(
            partial(_after_coordinate, max_rounds=b.coordinator_max_revise_rounds), "coordinate", ov
        ),
        ["scout", "short_plan", "long_plan", "escalation_gate", "oversee_spec"],
    )
    edge("scout", lambda s: "design", ["design"])
    edge(
        "design",
        _via_overseer(partial(_after_design, review_tests=b.gates.review_tests), "design", ov),
        [
            "tests_gate",
            "freeze",
            "design",
            "design_gate",
            "oversee_design",
            "oversee_design_gate",
        ],
    )
    g.add_edge("freeze", "code")
    edge(
        "code",
        _via_overseer(partial(_after_code, max_sessions=b.coder_max_sessions), "code", ov),
        ["tamper_gate", "opt_baseline", "dispute_gate", "code", "coder_gate", "oversee_code"],
    )
    edge("revise_test", _after_revise, ["code", "coder_gate"])
    edge("opt_baseline", _after_opt_baseline, ["digest", "optimize"])
    edge("optimize", _after_optimize, ["tamper_gate", "digest", "optimize"])
    edge(
        "digest",
        _via_overseer(lambda s: "result_gate", "digest", ov),
        ["result_gate", "oversee_result"],
    )
    targets = {
        "roadmap": ["short_plan", "long_plan", END],
        "escalation": ["scout", "long_plan", END],
        "dispute": ["revise_test", "long_plan", END],
        "tests": ["freeze", "design", END],
        "design": ["freeze", "long_plan", END],
        "coder": ["digest", "long_plan", END],
        "tamper": ["code", "long_plan", END],
        "result": ["long_plan", END],
    }
    for kind, dests in targets.items():
        g.add_conditional_edges(f"{kind}_gate", partial(_after_gate, gate=kind), dests)
    g.add_conditional_edges("publication_gate", _after_publication, ["publish", END])
    g.add_conditional_edges("budget_gate", _after_budget_gate, [*GUARDED, END])
    g.add_conditional_edges(
        "finish", lambda s: "publish" if b.publish.modes else END, ["publish", END]
    )
    edge("publish", lambda s: "publication_gate", ["publication_gate"])
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
        graph = build_graph(deps).compile(checkpointer=saver)
        deps.state_history = lambda: (
            snap.values
            for snap in graph.get_state_history({"configurable": {"thread_id": deps.run_id}})
        )
        yield graph
    finally:
        conn.close()
