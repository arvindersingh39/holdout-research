"""Runs a graph until it finishes or a gate is left pending. Shared by `r2r run` and `resume`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from r2r.graph.build import open_graph
from r2r.graph.gates import GateChannel, GateRequest

RECURSION_LIMIT = 500

__all__ = ["DriveResult", "config", "drive", "open_graph", "pending_gate"]


def config(run_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}


@dataclass(frozen=True)
class DriveResult:
    status: Literal["complete", "stopped", "paused", "running"]
    state: dict[str, Any]


def pending_gate(graph: CompiledStateGraph, run_id: str) -> GateRequest | None:
    for task in graph.get_state(config(run_id)).tasks:
        for intr in task.interrupts:
            return GateRequest.model_validate(intr.value)
    return None


def drive(graph: CompiledStateGraph, run_id: str, channel: GateChannel, start: bool) -> DriveResult:
    """Start (or continue) the run; answer each gate through `channel`.

    Returns when the run completes or stops, or `paused` when the channel leaves a gate pending.
    """
    cfg = config(run_id)
    inp: Any = {"run_id": run_id} if start else None
    while True:
        graph.invoke(inp, cfg)
        snapshot = graph.get_state(cfg)
        req = pending_gate(graph, run_id)
        if req is None:
            values = snapshot.values
            return DriveResult(values.get("status", "running"), values)
        decision = channel.ask(req, run_id)
        if decision is None:
            return DriveResult("paused", snapshot.values)
        inp = Command(resume=decision.model_dump(mode="json"))
