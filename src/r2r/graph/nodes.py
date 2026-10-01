"""Graph node functions. Each takes the state plus `RunDeps` and returns a state update.

Gate nodes do nothing before `interrupt()`, so LangGraph re-running them on resume is safe.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from r2r.agents.coordinator import run_coordinator
from r2r.agents.long_planner import run_long_planner
from r2r.agents.retriever import run_retriever
from r2r.agents.short_planner import run_short_planner
from r2r.contracts import (
    CriterionResult,
    HumanDecision,
    Milestone,
    MilestoneSpec,
    ResultDigest,
    Roadmap,
)
from r2r.graph.deps import RunDeps
from r2r.graph.gates import GateRequest, gate_request
from r2r.graph.render import render
from r2r.graph.routing import is_material_change, mark_done, next_milestone
from r2r.graph.state import PipelineState
from r2r.ingest.proposal import ingest_proposal
from r2r.retrieval.context_pack import build_pack

Update = dict[str, Any]
CONTEXT_K = 8


def _milestone(state: PipelineState) -> Milestone:
    m = next_milestone(state["roadmap"])
    if m is None:
        raise RuntimeError("no open milestone, but the graph routed to milestone work")
    return m


def _issues(state: PipelineState, target: str) -> list:
    v = state.get("verdict")
    if v is None or v.verdict != "revise":
        return []
    return [i for i in v.issues if i.target == target]


# --- work nodes ---------------------------------------------------------------------------


def retrieve(state: PipelineState, deps: RunDeps) -> Update:
    if state.get("brief") is not None:
        return {}
    ctx = deps.ctx()
    proposal = ingest_proposal(deps.proposal_dir, deps.blobs, deps.index, ctx)
    result = run_retriever(proposal, deps.blobs, deps.paths, ctx)
    return {
        "run_id": deps.run_id,
        "status": "running",
        "cycle": 1,
        "revise_rounds": 0,
        "proposal": proposal,
        "brief": result.brief,
        "approved_roadmap": None,
    }


def long_plan(state: PipelineState, deps: RunDeps) -> Update:
    old = state.get("roadmap")
    new = run_long_planner(
        state["brief"],
        old,
        state.get("digest"),
        state.get("human_note"),
        _issues(state, "roadmap"),
        deps.ctx(),
    )
    gates = deps.settings.budgets.gates
    approved = state.get("approved_roadmap")
    if approved is None:
        needs_gate = gates.approve_first_roadmap
    else:
        needs_gate = gates.roadmap_changes == "always" or is_material_change(approved, new)
    return {
        "roadmap": new,
        "approved_roadmap": approved if needs_gate else new,
        "human_note": None,
        "spec": None,
        "verdict": None,
    }


def short_plan(state: PipelineState, deps: RunDeps) -> Update:
    m = _milestone(state)
    query = f"{m.title}. " + " ".join(c.text for c in m.acceptance_criteria)
    budget = deps.settings.budgets.context_pack_tokens.short_planner
    pack = build_pack(query, budget, CONTEXT_K, deps.index, deps.blobs)
    issues = _issues(state, "spec")
    previous = state.get("spec") if issues else None
    spec = run_short_planner(state["roadmap"], m, pack, previous, issues, deps.ctx(m.id))
    return {"spec": spec}


def coordinate(state: PipelineState, deps: RunDeps) -> Update:
    m = _milestone(state)
    verdict = run_coordinator(state["spec"], state["roadmap"], m, deps.ctx(m.id))
    rounds = state.get("revise_rounds", 0) + (1 if verdict.verdict == "revise" else 0)
    return {"verdict": verdict, "revise_rounds": rounds}


def build_stub(state: PipelineState, deps: RunDeps) -> Update:
    """Stands in for math designer, coder and optimizer until Phases 5 and 6."""
    m = _milestone(state)
    rows = [r for r in deps.ledger.rows(deps.run_id) if r.milestone_id == m.id]
    digest = ResultDigest(
        created_by="digest",
        run_id=deps.run_id,
        milestone_id=m.id,
        status="met",
        criteria=[
            CriterionResult(id=c.id, passed=True, evidence="stub: no code built yet")
            for c in state["spec"].acceptance_criteria
        ],
        deviations="Build stub: tests and code are not generated until Phases 5-6.",
        cost_usd=deps.ledger.spent(deps.run_id, m.id),
        tokens=sum(r.input_tokens + r.output_tokens for r in rows),
    )
    return {"digest": digest}


def finish(state: PipelineState, deps: RunDeps) -> Update:
    return {"status": "complete"}


# --- gates -----------------------------------------------------------------------------


def _ask(req: GateRequest) -> HumanDecision:
    return HumanDecision.model_validate(interrupt(req.model_dump()))


def roadmap_gate(state: PipelineState, deps: RunDeps) -> Update:
    rm = state["roadmap"]
    first = state.get("approved_roadmap") is None
    title = "Approve the first roadmap" if first else f"Approve roadmap changes (v{rm.version})"
    d = _ask(gate_request("roadmap", title, rm))
    update: Update = {"human_decision": d}
    if d.action == "approve":
        update["approved_roadmap"] = rm
    elif d.action == "edit":
        if not isinstance(d.edited, Roadmap):
            raise ValueError("an edit at the roadmap gate must supply a Roadmap")
        update |= {"roadmap": d.edited, "approved_roadmap": d.edited}
    elif d.action == "redirect":
        update["human_note"] = d.note
    else:
        update["status"] = "stopped"
    return update


def escalation_gate(state: PipelineState, deps: RunDeps) -> Update:
    spec, verdict = state["spec"], state["verdict"]
    title = f"{spec.milestone_id}: spec still failing review after {state['revise_rounds']} rounds"
    req = GateRequest(
        gate="escalation",
        title=title,
        markdown=render(verdict) + "\n\n" + render(spec),
        contract_type="MilestoneSpec",
        contract_json=spec.model_dump_json(),
    )
    d = _ask(req)
    update: Update = {"human_decision": d}
    if d.action == "edit":
        if not isinstance(d.edited, MilestoneSpec):
            raise ValueError("an edit at the escalation gate must supply a MilestoneSpec")
        update["spec"] = d.edited
    elif d.action == "redirect":
        update |= {"human_note": d.note, "revise_rounds": 0}
    elif d.action == "stop":
        update["status"] = "stopped"
    return update


def result_gate(state: PipelineState, deps: RunDeps) -> Update:
    digest = state["digest"]
    d = _ask(
        gate_request(
            "result", f"Review the result of {digest.milestone_id}", digest, editable=False
        )
    )
    update: Update = {"human_decision": d}
    if d.action == "stop":
        return update | {"status": "stopped"}
    reset = {"spec": None, "verdict": None, "revise_rounds": 0, "human_note": d.note or None}
    if d.action in ("approve", "edit"):
        update |= reset | {
            "roadmap": mark_done(state["roadmap"], digest.milestone_id),
            "cycle": state.get("cycle", 1) + 1,
        }
    else:  # redirect: milestone stays open; the planner decides with the note
        update |= reset
    return update
