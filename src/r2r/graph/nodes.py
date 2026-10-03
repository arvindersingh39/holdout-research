"""Graph node functions. Each takes the state plus `RunDeps` and returns a state update.

Gate nodes do nothing before `interrupt()`, so LangGraph re-running them on resume is safe.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from r2r.agents.coder import CoderTools, run_coder_session
from r2r.agents.coordinator import run_coordinator
from r2r.agents.long_planner import run_long_planner
from r2r.agents.math_designer import (
    DesignError,
    reference_files,
    run_math_designer,
    write_design,
)
from r2r.agents.optimizer import notes, run_optimizer
from r2r.agents.overseer import collect_evidence, run_overseer
from r2r.agents.retriever import run_retriever
from r2r.agents.scout import run_scout
from r2r.agents.short_planner import run_short_planner
from r2r.contracts import (
    HumanDecision,
    Milestone,
    MilestoneSpec,
    OptimizationOutcome,
    Roadmap,
)
from r2r.contracts.oversight import OversightPoint
from r2r.digest.builder import build_digest, write_deviations
from r2r.graph.deps import RunDeps
from r2r.graph.gates import GateRequest, gate_request
from r2r.graph.render import render, render_attempts, render_files
from r2r.graph.routing import is_material_change, mark_done, next_milestone
from r2r.graph.state import PipelineState
from r2r.harness.bench import BenchFailed
from r2r.harness.checks import run_checks
from r2r.harness.frozen import FrozenTamper, freeze, restore
from r2r.harness.gitops import Git
from r2r.harness.hotspots import hotspot_source
from r2r.harness.optimize import Baseline, baseline, try_patch
from r2r.harness.profile import run_profile
from r2r.harness.redgreen import assess
from r2r.harness.refdata import DATA_DIR
from r2r.harness.refdata import describe as describe_data
from r2r.harness.refdata import install as install_data
from r2r.harness.refdata import snapshot as snapshot_data
from r2r.harness.reference import ReferenceError, check_reference, store_reference
from r2r.harness.stoprule import decide
from r2r.harness.tests import CaseOutcome, run_tests, run_tests_detailed, test_names
from r2r.ingest.documents import wrap_document
from r2r.ingest.proposal import ingest_proposal
from r2r.llm.gateway import BudgetExceeded, ContractViolation
from r2r.llm.tokens import estimate_tokens
from r2r.publish.collect import collect_from_states
from r2r.publish.run import run_publish
from r2r.retrieval.context_pack import build_pack

Update = dict[str, Any]


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
        deps.ctx(briefing=_briefing(state, "long_planner")),
        proposal=_proposal_text(state, deps),
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
    pack = build_pack(query, budget, deps.settings.budgets.context_pack.k, deps.index, deps.blobs)
    issues = _issues(state, "spec")
    previous = state.get("spec") if issues else None
    spec = run_short_planner(
        state["roadmap"],
        m,
        pack,
        previous,
        issues,
        deps.ctx(m.id, _briefing(state, "short_planner")),
        data_files=[f"{DATA_DIR}/{n}" for n in _reference_data(deps)],
        proposal=_proposal_text(state, deps),
    )
    return {"spec": spec}


def coordinate(state: PipelineState, deps: RunDeps) -> Update:
    m = _milestone(state)
    verdict = run_coordinator(state["spec"], state["roadmap"], m, deps.ctx(m.id))
    rounds = state.get("revise_rounds", 0) + (1 if verdict.verdict == "revise" else 0)
    return {"verdict": verdict, "revise_rounds": rounds}


def _proposal_text(state: PipelineState, deps: RunDeps) -> str:
    """The owner's proposal in full when it is short (excerpts alone made planners think a fully
    specified model was missing); "" when it is long, leaving retrieval to supply excerpts."""
    p = state.get("proposal")
    if p is None:
        return ""
    text = deps.blobs.get_text(p.text_ref)
    if estimate_tokens(text) > deps.settings.budgets.context_pack.full_proposal_tokens:
        return ""
    return wrap_document(text, source="proposal.md")


def _reference_data(deps: RunDeps) -> list[str]:
    """D71: snapshot the proposal's data/ folder into the run (once); return the file names."""
    return snapshot_data(deps.proposal_dir, deps.paths.root, deps.settings.budgets.reference_data)


def _existing_files(deps: RunDeps) -> list[str]:
    root = deps.workspace().root
    return sorted(
        p.relative_to(root).as_posix()
        for d in ("src", "bench", "tests/frozen", "checks")
        for p in (root / d).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def scout(state: PipelineState, deps: RunDeps) -> Update:
    """Published baselines for this milestone's tests, quote-verified (D52). Never blocks a run."""
    m = _milestone(state)
    if state.get("baseline_note") is not None and all(
        b.milestone_id == m.id for b in state.get("baselines", [])
    ):
        return {}  # already scouted for this milestone (e.g. after an escalation)
    res = run_scout(
        state["spec"], m, state["roadmap"].goal, deps.search(), deps.blobs, deps.ctx(m.id)
    )
    return {"baselines": res.cards, "baseline_note": res.note}


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
        deps.settings.budgets.context_pack.k,
        deps.index,
        deps.blobs,
    )
    prev = state.get("math_spec")
    previous = prev if prev is not None and prev.milestone_id == m.id else None
    failed = state.get("design_attempts", 0)
    _reference_data(deps)
    install_data(ws, deps.paths.root)
    try:
        out = run_math_designer(
            spec,
            m,
            pack,
            _existing_files(deps),
            state.get("design_feedback"),
            deps.ctx(m.id, _briefing(state, "math_designer")),
            baselines=state.get("baselines", []),
            baseline_note=state.get("baseline_note") or "",
            data_files=describe_data(
                deps.paths.root, deps.settings.budgets.reference_data.agent_chars
            ),
            proposal=_proposal_text(state, deps),
        )
    except ContractViolation as e:  # unusable output: a failed design attempt, not a crash
        return {
            "redgreen_ok": False,
            "design_attempts": failed + 1,
            "design_feedback": f"The previous answer was an invalid design: {str(e)[:600]}",
        }
    try:
        write_design(ws, out, previous)
        refs = store_reference(deps.reference_dir(m.id), reference_files(out))
    except (DesignError, ReferenceError) as e:
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
    ref_cases = None
    if refs:
        _, ref_cases = check_reference(
            ws,
            deps.reference_dir(m.id),
            deps.sandbox_for,
            run_id=deps.run_id,
            milestone_id=m.id,
            timeout_s=timeout,
        )
    rg = assess(
        [c.id for c in spec.acceptance_criteria],
        cases,
        checks,
        out.spec.test_files,
        reference_cases=ref_cases,
    )
    return {
        "math_spec": out.spec,
        "redgreen_ok": rg.ok,
        "test_result": result,
        "design_attempts": failed if rg.ok else failed + 1,
        "design_feedback": None if rg.ok else rg.describe(),
        "perf_unmet": rg.perf_unmet,
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
    session = run_coder_session(
        state["spec"],
        state["math_spec"],
        state.get("test_result"),
        tools,
        deps.ctx(m.id, _briefing(state, "coder")),
        model=model,
    )
    attempts = state.get("tamper_attempts", 0) + len(tools.tamper_attempts)
    update: Update = {
        "coder_sessions": n + 1,
        "tamper_attempts": attempts,
        "coder_message": (session.loop.final_text or "")[:1500] or None,
    }
    if tools.disputes:
        d = tools.disputes[-1]
        file = d.test_id.split("::", 1)[0].replace(".", "/") + ".py" if "::" in d.test_id else None
        update |= {
            "dispute": f"**{d.test_id}**\n\nReason: {d.reason}\n\nEvidence: {d.evidence}",
            "dispute_file": file,
        }
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
    try:
        b = baseline(
            deps.workspace(),
            deps.sandbox(),
            ms.bench.entrypoint,
            deps.settings.budgets,
            run_id=deps.run_id,
            milestone_id=m.id,
        )
    except BenchFailed as e:  # e.g. a timeout: the tests passed, so skip optimising, keep going
        return reset | {"opt_stop_reason": f"benchmark failed: {e}", "opt_baseline_s": None}
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
            deps.ctx(m.id, _briefing(state, "optimizer")),
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
    if any(not c.passed for c in d.criteria):
        note = write_deviations(state["spec"], d, result, deps.ctx(m.id))
        extra = (
            f" Optimizer stopped: {state['opt_stop_reason']}."
            if state.get("opt_stop_reason")
            else ""
        )
        d = d.model_validate({**d.model_dump(), "deviations": (note + extra)[:500]})
    return {"digest": d, "test_result": result}


def finish(state: PipelineState, deps: RunDeps) -> Update:
    return {"status": "complete"}


def publish(state: PipelineState, deps: RunDeps) -> Update:
    """Phase 8: paper and package from every finished milestone's contracts."""
    history = deps.state_history() if deps.state_history else []
    record = collect_from_states([state, *history])
    bundles, md = run_publish(
        record,
        deps.workspace(),
        deps.paths.root / "publish",
        deps.sandbox_for,
        deps.ctx(),
        deps.blobs,
        deps.settings.budgets.publish.modes,
        note=state.get("publish_note"),
    )
    return {"publication": bundles, "publication_md": md, "publish_note": None}


def publication_gate(state: PipelineState, deps: RunDeps) -> Update:
    revisions = state.get("publish_revisions", 0)
    md = state.get("publication_md") or "Nothing was published."
    if revisions >= 1:
        md += "\n\nThe one revision pass is used: approve, or stop and run `r2r publish` again."
    d = _ask(
        state, deps, GateRequest(gate="publication", title="Review the publication", markdown=md)
    )
    update: Update = {"human_decision": d}
    if d.action == "stop":
        return update | {"status": "stopped"}
    if d.action == "redirect":
        return update | {"publish_note": d.note, "publish_revisions": revisions + 1}
    return update | {"status": "complete"}


# --- gates -----------------------------------------------------------------------------


def _ask(state: PipelineState, deps: RunDeps, req: GateRequest) -> HumanDecision:
    md = render_oversight(state, deps.settings.budgets.overseer.flags_shown)
    if md:
        req = req.model_copy(update={"markdown": f"{req.markdown}\n\n{md}"})
    return HumanDecision.model_validate(interrupt(req.model_dump()))


def render_oversight(state: PipelineState, flags_shown: int) -> str:
    """Phase 7c: what the overseer currently tells each agent, and its newest flags."""
    briefings, flags = state.get("briefings") or {}, state.get("flags") or []
    if not briefings and not flags:
        return ""
    lines = ["**Overseer** (advice only; it cannot route or approve)"]
    for f in reversed(flags[-flags_shown:]):
        lines.append(f"- ⚑ {f.severity}: {f.text} [{', '.join(f.evidence)}]")
    for role, text in briefings.items():
        lines.append(f"- briefing for {role}: {text}")
    return "\n".join(lines)


def _briefing(state: PipelineState, role: str) -> str | None:
    return (state.get("briefings") or {}).get(role)


PLANNERS = ("long_planner", "short_planner")


def oversee(state: PipelineState, deps: RunDeps, point: OversightPoint) -> Update:
    """A hook point (Phase 7c). Python decided to come here and decides where to go next; the
    overseer only updates briefings and flags."""
    lim = deps.settings.budgets.overseer
    m = next_milestone(state["roadmap"]) if point != "plan" else None
    files: dict[str, str] = {}
    ms = state.get("math_spec")
    if point in ("design", "design_gate", "code") and ms is not None:
        root = deps.workspace().root
        files = {p: (root / p).read_text() for p in ms.test_files if (root / p).is_file()}
    data = describe_data(deps.paths.root, deps.settings.budgets.reference_data.agent_chars)
    evidence = collect_evidence(state, point, files, lim.evidence_chars, data=data)
    mid = m.id if m is not None else None
    report = run_overseer(
        point,
        state["roadmap"].goal,
        mid,
        evidence,
        dict(state.get("briefings") or {}),
        deps.ctx(mid),
    )
    # at a result review only its own advice stands: earlier briefings and flags are about work
    # that is now finished
    fresh = point == "result"
    briefings = {} if fresh else dict(state.get("briefings") or {})
    carry = dict(state.get("carry_briefings") or {})
    for b in report.briefings:
        briefings[b.role] = b.text
        if point == "result" and b.role in PLANNERS:
            carry[b.role] = b.text
    return {
        "briefings": briefings,
        "carry_briefings": carry,
        "flags": [*([] if fresh else state.get("flags") or []), *report.flags],
        "oversee_calls": state.get("oversee_calls", 0) + 1,
    }


def roadmap_gate(state: PipelineState, deps: RunDeps) -> Update:
    rm = state["roadmap"]
    first = state.get("approved_roadmap") is None
    title = "Approve the first roadmap" if first else f"Approve roadmap changes (v{rm.version})"
    d = _ask(state, deps, gate_request("roadmap", title, rm))
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
    d = _ask(state, deps, req)
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
        state,
        deps,
        GateRequest(
            gate="result", title=f"Review the result of {digest.milestone_id}", markdown=md
        ),
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
        "baselines": [],
        "baseline_note": None,
        "perf_unmet": [],
        "briefings": dict(state.get("carry_briefings") or {}),
        "carry_briefings": {},
        "flags": [],
        "oversee_calls": 0,
        "dispute": None,
        "dispute_file": None,
        "coder_message": None,
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
    md = (
        render(ms) + "\n\nAll criteria fail on the stubs with NotImplementedError, and every "
        "correctness test passes on the designer's reference solution.\n\n"
        + _render_perf(test_names(deps.workspace().root, ms.test_files), state)
        + _render_baselines(state)
        + "\n\n"
        + files
    )
    d = _ask(
        state,
        deps,
        GateRequest(
            gate="tests", title=f"Review {ms.milestone_id}'s tests before coding", markdown=md
        ),
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
        state,
        deps,
        GateRequest(gate="design", title="Tests are not red for the right reason", markdown=md),
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
    if state.get("coder_message"):
        md += f"\n\n**The coder's last message**\n\n{state['coder_message']}"
    d = _ask(
        state,
        deps,
        GateRequest(gate="coder", title=f"{r.milestone_id}: tests still failing", markdown=md),
    )
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
    d = _ask(state, deps, GateRequest(gate="tamper", title="Tamper detected", markdown=md))
    update: Update = {"human_decision": d}
    if d.action == "stop":
        return update | {"status": "stopped"}
    if d.action == "redirect":
        return update | {"human_note": d.note}
    ws = deps.workspace()
    restore(ws, Git(ws), state["frozen_commit"])
    return update | {"tamper": None}


def budget_gate(state: PipelineState, deps: RunDeps) -> Update:
    """D46: a step would overspend. Shows spend; approve retries the blocked step."""
    blocked = state.get("budget_blocked") or "?"
    by_role = deps.ledger.by_role(deps.run_id)
    rows = "\n".join(f"- {r}: ${s.cost_usd:.4f} ({s.calls} calls)" for r, s in by_role.items())
    by_ms = "\n".join(
        f"- {m or 'run-level'}: ${c:.4f}" for m, c in deps.ledger.by_milestone(deps.run_id).items()
    )
    caps = deps.settings.budgets
    md = (
        f"## Budget exceeded at `{blocked}`\n\n{state.get('budget_message', '')}\n\n"
        f"Caps: ${caps.usd_cap_milestone:.2f} per milestone, ${caps.usd_cap_run:.2f} per run. "
        f"Spent so far: ${deps.ledger.spent(deps.run_id):.4f}.\n\n"
        f"**Spend by role**\n{rows or '- (none)'}\n\n**Spend by milestone**\n{by_ms or '- (none)'}"
        "\n\nTo continue: quit, raise the caps in `config/budgets.yaml`, `r2r resume`, then "
        "approve to retry the blocked step."
    )
    d = _ask(
        state, deps, GateRequest(gate="budget", title=f"Budget exceeded at {blocked}", markdown=md)
    )
    update: Update = {"human_decision": d, "budget_blocked": None, "budget_message": None}
    if d.action == "stop":
        return update | {"status": "stopped"}
    if d.action == "redirect":
        return update | {"human_note": d.note, "budget_retry": "long_plan"}
    return update | {"budget_retry": blocked}


def _render_perf(names: list[str], state: PipelineState) -> str:
    """D60: speed targets are listed apart so a weakened number is easy to spot."""
    perf = [n for n in names if CaseOutcome("", "", n, "passed", "").is_perf]
    if not perf:
        return ""
    unmet = {t.rsplit("::", 1)[-1] for t in state.get("perf_unmet", [])}
    lines = ["**Performance tests** (targets the coder must meet; compare with the criteria)"]
    for n in perf:
        note = " · not met by the reference (allowed)" if n in unmet else ""
        lines.append(f"- {n}{note}")
    return "\n".join(lines) + "\n\n"


def _render_baselines(state: PipelineState) -> str:
    cards = state.get("baselines", [])
    lines = [f"**Published baselines** ({state.get('baseline_note') or 'none'})"]
    for b in cards:
        val = f"{b.value:g} {b.units}".strip() if b.value is not None else "(qualitative)"
        lines.append(f'- {b.criterion_id}: {b.claim} = {val} · "{b.quote}" · {b.source_url}')
    return "\n".join(lines)


def dispute_gate(state: PipelineState, deps: RunDeps) -> Update:
    """D56: the coder says a frozen test can never pass. Approve lets #4 revise that file only."""
    md = f"## The coder disputes a frozen test\n\n{state.get('dispute')}"
    if state.get("coder_message"):
        md += f"\n\n**The coder's last message**\n\n{state['coder_message']}"
    if state.get("test_result") is not None:
        md += "\n\n" + render(state["test_result"])
    if state.get("dispute_file"):
        md += "\n\n" + render_files(deps.workspace().root, [state["dispute_file"]])
    md += (
        "\n\nApprove lets the math designer revise only this test file; the revision must "
        "pass on its reference solution and is re-frozen and committed as owner-approved."
    )
    d = _ask(
        state, deps, GateRequest(gate="dispute", title="Test disputed by the coder", markdown=md)
    )
    update: Update = {"human_decision": d}
    if d.action == "stop":
        return update | {"status": "stopped"}
    if d.action == "redirect":
        return update | {"human_note": d.note, "dispute": None}
    return update


def revise_test(state: PipelineState, deps: RunDeps) -> Update:
    """#4 rewrites only the disputed test file; it must pass on the stored reference (D56)."""
    m = _milestone(state)
    file = state.get("dispute_file")
    ws = deps.workspace()
    git = Git(ws)
    feedback = (
        f"The owner accepted the coder's dispute of a frozen test:\n{state.get('dispute')}\n"
        f"Rewrite ONLY {file}. Keep every other file exactly as it is."
    )
    fail: Update = {"dispute": None, "coder_message": "Test revision failed; see the dispute."}
    if not file or not deps.reference_dir(m.id).is_dir():
        return fail | {"coder_message": "No reference solution stored; the test cannot be revised."}
    try:
        out = run_math_designer(
            state["spec"],
            m,
            build_pack("", 1, 1, deps.index, deps.blobs),
            _existing_files(deps),
            feedback,
            deps.ctx(m.id, _briefing(state, "math_designer")),
            baselines=state.get("baselines", []),
            data_files=describe_data(
                deps.paths.root, deps.settings.budgets.reference_data.agent_chars
            ),
            proposal=_proposal_text(state, deps),
        )
    except ContractViolation:
        return fail
    new = [f for f in out.files if f.path == file and f.purpose == "test"]
    if not new:
        return fail
    target = ws.root / file
    target.chmod(0o644)
    target.write_text(new[0].content)
    timeout = deps.settings.budgets.sandbox.test_timeout_s
    _, ref_cases = check_reference(
        ws,
        deps.reference_dir(m.id),
        deps.sandbox_for,
        run_id=deps.run_id,
        milestone_id=m.id,
        timeout_s=timeout,
    )
    if not ref_cases or any(c.misses_reference for c in ref_cases):
        git.run("checkout", "HEAD", "--", file)  # revert to the frozen version
        freeze(ws)
        return fail | {"coder_message": "The revised test still fails on the reference solution."}
    freeze(ws)
    commit = git.commit(f"{m.id}: owner-approved revision of {file} after a coder dispute")
    return {
        "dispute": None,
        "dispute_file": None,
        "coder_sessions": 0,
        "frozen_commit": commit,
        "coder_message": None,
    }
