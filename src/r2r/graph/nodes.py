"""Graph node functions. Each takes the state plus `RunDeps` and returns a state update.

Gate nodes do nothing before `interrupt()`, so LangGraph re-running them on resume is safe.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from r2r.agents.coder import CoderTools, run_coder_session
from r2r.agents.coordinator import run_coordinator
from r2r.agents.long_planner import run_long_planner
from r2r.agents.math_designer import DesignError, run_math_designer, write_design
from r2r.agents.optimizer import notes, run_optimizer
from r2r.agents.retriever import run_retriever
from r2r.agents.short_planner import run_short_planner
from r2r.contracts import (
    HumanDecision,
    Milestone,
    MilestoneSpec,
    OptimizationOutcome,
    Roadmap,
)
from r2r.digest.builder import build_digest
from r2r.graph.deps import RunDeps
from r2r.graph.gates import GateRequest, gate_request
from r2r.graph.render import render, render_attempts, render_files
from r2r.graph.routing import is_material_change, mark_done, next_milestone
from r2r.graph.state import PipelineState
from r2r.harness.checks import run_checks
from r2r.harness.frozen import FrozenTamper, freeze, restore
from r2r.harness.gitops import Git
from r2r.harness.hotspots import hotspot_source
from r2r.harness.optimize import Baseline, baseline, try_patch
from r2r.harness.profile import run_profile
from r2r.harness.redgreen import assess
from r2r.harness.stoprule import decide
from r2r.harness.tests import run_tests, run_tests_detailed
from r2r.ingest.proposal import ingest_proposal
from r2r.llm.gateway import BudgetExceeded, ContractViolation
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


def _existing_files(deps: RunDeps) -> list[str]:
    root = deps.workspace().root
    return sorted(
        p.relative_to(root).as_posix()
        for d in ("src", "bench", "tests/frozen", "checks")
        for p in (root / d).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def design(state: PipelineState, deps: RunDeps) -> Update:
    """#4 writes tests, checks, stubs and bench; the harness proves red before green (D32)."""
    m = _milestone(state)
    spec = state["spec"]
    ws, box = deps.workspace(), deps.sandbox()
    Git(ws).checkout("main")
    query = f"{m.title}. " + " ".join(c.text for c in m.acceptance_criteria)
    pack = build_pack(
        query,
        deps.settings.budgets.context_pack_tokens.math_designer,
        CONTEXT_K,
        deps.index,
        deps.blobs,
    )
    prev = state.get("math_spec")
    previous = prev if prev is not None and prev.milestone_id == m.id else None
    failed = state.get("design_attempts", 0)
    out = run_math_designer(
        spec, m, pack, _existing_files(deps), state.get("design_feedback"), deps.ctx(m.id)
    )
    try:
        write_design(ws, out, previous)
    except DesignError as e:
        return {
            "redgreen_ok": False,
            "design_attempts": failed + 1,
            "design_feedback": f"The design could not be written: {e}",
        }
    timeout = deps.settings.budgets.sandbox.test_timeout_s
    checks = run_checks(ws, box, timeout)
    result, cases = run_tests_detailed(
        ws, box, run_id=deps.run_id, milestone_id=m.id, timeout_s=timeout, require_frozen=False
    )
    rg = assess([c.id for c in spec.acceptance_criteria], cases, checks, out.spec.test_files)
    return {
        "math_spec": out.spec,
        "redgreen_ok": rg.ok,
        "test_result": result,
        "design_attempts": failed if rg.ok else failed + 1,
        "design_feedback": None if rg.ok else rg.describe(),
    }


def freeze_tests(state: PipelineState, deps: RunDeps) -> Update:
    m = _milestone(state)
    ws = deps.workspace()
    git = Git(ws)
    freeze(ws)
    commit = git.commit(f"{m.id}: frozen tests, checks, stubs and bench")
    git.branch(f"wip/{m.id}", commit)
    return {"frozen_commit": commit, "coder_sessions": 0, "tamper": None, "design_feedback": None}


def code(state: PipelineState, deps: RunDeps) -> Update:
    """One coder session; the harness reruns the tests afterwards (never trusting the session)."""
    m = _milestone(state)
    b = deps.settings.budgets
    cfg = deps.settings.models.roles["coder"]
    n = state.get("coder_sessions", 0)
    model = cfg.escalate_to if cfg.escalate_to and n >= b.coder_escalate_after else None
    ws, box = deps.workspace(), deps.sandbox()
    tools = CoderTools(
        ws, box, run_id=deps.run_id, milestone_id=m.id, timeout_s=b.sandbox.test_timeout_s
    )
    run_coder_session(
        state["spec"],
        state["math_spec"],
        state.get("test_result"),
        tools,
        deps.ctx(m.id),
        model=model,
    )
    attempts = state.get("tamper_attempts", 0) + len(tools.tamper_attempts)
    update: Update = {"coder_sessions": n + 1, "tamper_attempts": attempts}
    try:
        final = run_tests(
            ws, box, run_id=deps.run_id, milestone_id=m.id, timeout_s=b.sandbox.test_timeout_s
        )
    except FrozenTamper as e:
        return update | {"tamper": str(e)}
    git = Git(ws)
    git.commit(f"{m.id}: coder session {n + 1} ({'pass' if final.passed else 'fail'})")
    if final.passed:
        git.checkout("main")
        git.run("merge", "-q", "--ff-only", f"wip/{m.id}")
    return update | {"test_result": final, "tamper": None}


def _target(state: PipelineState, deps: RunDeps) -> float | None:
    bench = state["math_spec"].bench
    if bench is not None and bench.target_runtime_s is not None:
        return bench.target_runtime_s
    return deps.settings.budgets.optimizer_target_runtime_s


def opt_baseline(state: PipelineState, deps: RunDeps) -> Update:
    """Measure the passing code before optimising: bench, lines of code, profile, bench hash."""
    ms = state["math_spec"]
    reset: Update = {"opt_outcomes": [], "opt_patches": [], "opt_stop_reason": None}
    if ms.bench is None:
        return reset | {"opt_stop_reason": "no benchmark", "opt_baseline_s": None}
    m = _milestone(state)
    b = baseline(
        deps.workspace(),
        deps.sandbox(),
        ms.bench.entrypoint,
        deps.settings.budgets,
        run_id=deps.run_id,
        milestone_id=m.id,
    )
    action, reason = decide([], deps.settings.budgets, _target(state, deps), b.runtime_s)
    return reset | {
        "opt_baseline_s": b.runtime_s,
        "opt_runtime_s": b.runtime_s,
        "opt_sloc": b.sloc,
        "opt_bench_hash": b.bench_hash,
        "opt_profile": b.profile,
        "opt_stop_reason": reason if action == "stop" else None,
    }


def optimize(state: PipelineState, deps: RunDeps) -> Update:
    """One optimizer attempt: #6 proposes, the harness keeps or reverts, the stop rule decides."""
    m = _milestone(state)
    ms = state["math_spec"]
    cfg = deps.settings.budgets
    ws, box = deps.workspace(), deps.sandbox()
    outcomes = list(state.get("opt_outcomes", []))
    patches = list(state.get("opt_patches", []))
    attempt = len(outcomes) + 1
    current = Baseline(
        state["opt_runtime_s"],
        0.0,
        state["opt_sloc"],
        state["opt_bench_hash"],
        state["opt_profile"],
    )
    target = _target(state, deps)
    try:
        patch = run_optimizer(
            current.profile,
            hotspot_source(ws, current.profile),
            current.runtime_s,
            target,
            ms,
            notes(patches, outcomes),
            deps.ctx(m.id),
        )
    except BudgetExceeded as e:
        return {"opt_stop_reason": f"budget exhausted ({e})"}  # D40
    except ContractViolation as e:
        patch = None
        outcome = OptimizationOutcome(
            created_by="harness",
            run_id=deps.run_id,
            milestone_id=m.id,
            attempt=attempt,
            accepted=False,
            reason=f"rejected: no valid patch ({str(e)[:200]})",
            runtime_before_s=current.runtime_s,
            sloc_before=current.sloc,
        )
    else:
        try:
            outcome = try_patch(
                ws,
                box,
                Git(ws),
                patch,
                current,
                attempt,
                ms.bench.entrypoint,
                cfg,
                run_id=deps.run_id,
                milestone_id=m.id,
            )
        except FrozenTamper as e:
            return {"tamper": str(e)}
    update: Update = {}
    if outcome.accepted:
        profile = run_profile(
            ws,
            box,
            ms.bench.entrypoint,
            cfg.sandbox.bench_timeout_s,
            run_id=deps.run_id,
            milestone_id=m.id,
        )
        update |= {
            "opt_runtime_s": outcome.runtime_after_s,
            "opt_sloc": outcome.sloc_after,
            "opt_profile": profile,
        }
    outcomes.append(outcome)
    patches.append(patch)
    action, reason = decide(outcomes, cfg, target, state["opt_baseline_s"])
    return update | {
        "opt_outcomes": outcomes,
        "opt_patches": patches,
        "opt_stop_reason": reason if action == "stop" else None,
    }


def digest(state: PipelineState, deps: RunDeps) -> Update:
    m = _milestone(state)
    ws, box = deps.workspace(), deps.sandbox()
    result, cases = run_tests_detailed(
        ws,
        box,
        run_id=deps.run_id,
        milestone_id=m.id,
        timeout_s=deps.settings.budgets.sandbox.test_timeout_s,
    )
    d = build_digest(
        state["spec"],
        cases,
        deps.ledger,
        run_id=deps.run_id,
        milestone_id=m.id,
        test_files=state["math_spec"].test_files,
        runtime_baseline_s=state.get("opt_baseline_s"),
        runtime_final_s=state.get("opt_runtime_s"),
        optimizer_attempts=len(state.get("opt_outcomes", [])),
        optimizer_stop=state.get("opt_stop_reason"),
    )
    return {"digest": d, "test_result": result}


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
    md = render(digest)
    attempts = render_attempts(state.get("opt_patches", []), state.get("opt_outcomes", []))
    if attempts:
        md += "\n\n" + attempts
    d = _ask(
        GateRequest(gate="result", title=f"Review the result of {digest.milestone_id}", markdown=md)
    )
    update: Update = {"human_decision": d}
    if d.action == "stop":
        return update | {"status": "stopped"}
    reset = {
        "spec": None,
        "verdict": None,
        "revise_rounds": 0,
        "human_note": d.note or None,
        "math_spec": None,
        "design_attempts": 0,
        "design_feedback": None,
        "frozen_commit": None,
        "test_result": None,
        "coder_sessions": 0,
        "tamper": None,
        "opt_outcomes": [],
        "opt_patches": [],
        "opt_baseline_s": None,
        "opt_runtime_s": None,
        "opt_profile": None,
        "opt_stop_reason": None,
        "opt_bench_hash": None,
    }
    if d.action in ("approve", "edit"):
        update |= reset | {
            "roadmap": mark_done(state["roadmap"], digest.milestone_id),
            "cycle": state.get("cycle", 1) + 1,
        }
    else:  # redirect: milestone stays open; the planner decides with the note
        update |= reset
    return update


def tests_gate(state: PipelineState, deps: RunDeps) -> Update:
    ms = state["math_spec"]
    files = render_files(deps.workspace().root, [*ms.test_files, *ms.check_files])
    md = render(ms) + "\n\nAll criteria fail on the stubs with NotImplementedError.\n\n" + files
    d = _ask(
        GateRequest(
            gate="tests", title=f"Review {ms.milestone_id}'s tests before coding", markdown=md
        )
    )
    if d.action == "stop":
        return {"human_decision": d, "status": "stopped"}
    if d.action == "redirect":
        return {
            "human_decision": d,
            "design_attempts": 0,
            "design_feedback": f"The owner reviewed the tests and asks: {d.note}",
        }
    return {"human_decision": d}


def design_gate(state: PipelineState, deps: RunDeps) -> Update:
    ms = state.get("math_spec")
    md = f"## The math designer failed twice\n\n{state.get('design_feedback') or ''}"
    if ms is not None:
        md += "\n\n" + render(ms)
    d = _ask(
        GateRequest(gate="design", title="Tests are not red for the right reason", markdown=md)
    )
    update: Update = {"human_decision": d}
    if d.action == "stop":
        update["status"] = "stopped"
    elif d.action == "redirect":
        update["human_note"] = d.note
    return update


def coder_gate(state: PipelineState, deps: RunDeps) -> Update:
    r = state["test_result"]
    md = f"{state.get('coder_sessions', 0)} coder sessions used.\n\n" + render(r)
    d = _ask(GateRequest(gate="coder", title=f"{r.milestone_id}: tests still failing", markdown=md))
    update: Update = {"human_decision": d}
    if d.action == "stop":
        update["status"] = "stopped"
    elif d.action == "redirect":
        update["human_note"] = d.note
    else:  # accept the partial result: keep the coder's work on main
        m = _milestone(state)
        git = Git(deps.workspace())
        git.checkout("main")
        git.run("merge", "-q", "--ff-only", f"wip/{m.id}")
    return update


def tamper_gate(state: PipelineState, deps: RunDeps) -> Update:
    md = (
        f"## Frozen tests were modified\n\n{state.get('tamper')}\n\n"
        f"Blocked write attempts so far: {state.get('tamper_attempts', 0)}.\n\n"
        "Approve restores the frozen files from git and lets the coder continue."
    )
    d = _ask(GateRequest(gate="tamper", title="Tamper detected", markdown=md))
    update: Update = {"human_decision": d}
    if d.action == "stop":
        return update | {"status": "stopped"}
    if d.action == "redirect":
        return update | {"human_note": d.note}
    ws = deps.workspace()
    restore(ws, Git(ws), state["frozen_commit"])
    return update | {"tamper": None}
