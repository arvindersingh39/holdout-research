"""Graph node functions. Each takes the state plus `RunDeps` and returns a state update.

Gate nodes do nothing before `interrupt()`, so LangGraph re-running them on resume is safe.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import partial
from typing import Any

from langgraph.types import interrupt

from r2r.agents.coder import CoderTools, run_coder_session
from r2r.agents.coordinator import run_coordinator
from r2r.agents.long_planner import revise_roadmap, run_long_planner
from r2r.agents.math_designer import (
    DesignError,
    revise_design,
    run_math_designer,
    write_design,
)
from r2r.agents.overseer import collect_evidence, run_critique, run_overseer
from r2r.agents.retriever import run_retriever
from r2r.agents.short_planner import run_short_planner
from r2r.contracts import (
    Critique,
    HumanDecision,
    MathDesign,
    Milestone,
    OversightFlag,
    Roadmap,
    VerificationReport,
)
from r2r.contracts.oversight import OversightPoint
from r2r.digest.builder import build_digest, write_deviations
from r2r.graph.deps import RunDeps
from r2r.graph.gates import GateRequest, gate_request
from r2r.graph.render import render, render_review
from r2r.graph.routing import (
    advance_without_replan,
    current_on_open,
    is_material_change,
    mark_done,
    next_milestone,
)
from r2r.graph.state import PipelineState
from r2r.harness.autopilot import Facts
from r2r.harness.autopilot import decide as decide_gate
from r2r.harness.benchmarks import (
    BenchmarkFileError,
    check_results,
    coder_view,
    default_results,
    expected_refs,
    load_reference,
    outline,
    results_file,
    write_benchmarks,
)
from r2r.harness.gitops import Git
from r2r.harness.refdata import snapshot as snapshot_data
from r2r.harness.stubcheck import check_code, record_stubs, reproduce_script
from r2r.ingest.documents import wrap_document
from r2r.ingest.proposal import ingest_proposal
from r2r.llm.gateway import ContractViolation
from r2r.llm.tokens import estimate_tokens
from r2r.log import get_logger
from r2r.publish.collect import collect_from_states
from r2r.publish.run import run_publish
from r2r.retrieval.context_pack import build_pack

Update = dict[str, Any]

log = get_logger("r2r.graph.nodes")


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
    _reference_data(deps)  # the owner's data/ first: the snapshot is taken once
    if result.brief.benchmarks:
        write_benchmarks(deps.paths.root, result.brief.benchmarks)  # D93
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
    kept = advance_without_replan(
        old, state.get("digest"), state.get("human_note"), _issues(state, "roadmap")
    )
    if kept is not None:  # the plan stands: no model call, no roadmap gate
        log.info(
            "roadmap kept; next milestone starts", extra={"milestone": kept.current_milestone_id}
        )
        return {
            "roadmap": kept,
            "approved_roadmap": kept,
            "human_note": None,
            "spec": None,
            "verdict": None,
            "split_request": False,
        }
    new = run_long_planner(
        state["brief"],
        old,
        state.get("digest"),
        state.get("human_note"),
        _issues(state, "roadmap"),
        deps.ctx(briefing=_briefing(state, "long_planner")),
        proposal=_proposal_text(state, deps),
        benchmarks=_outline(deps),
        known_ref=partial(_known_ref, _reference(deps)),
    )
    new = current_on_open(new)
    cleared = {"human_note": None, "spec": None, "verdict": None, "split_request": False}
    if _reflect(state, deps, "plan") and next_milestone(new) is not None:
        # D94: the draft is critiqued and revised once before the gate decision
        return cleared | {"roadmap": new, "plan_base": old, "plan_review": True}
    return cleared | {"roadmap": new, "approved_roadmap": _approved(state, deps, new)}


def _approved(state: PipelineState, deps: RunDeps, new: Roadmap) -> Roadmap | None:
    """The roadmap counted as approved: the new one when no gate is needed."""
    gates = deps.settings.budgets.gates
    approved = state.get("approved_roadmap")
    if approved is None:
        needs_gate = gates.approve_first_roadmap
    else:
        needs_gate = gates.roadmap_changes == "always" or is_material_change(approved, new)
    return approved if needs_gate else new


def _reflect(state: PipelineState, deps: RunDeps, kind: str) -> bool:
    """D94: one overseer critique of a new roadmap or design, when enabled and under the cap."""
    ov = deps.settings.budgets.overseer
    on = ov.enabled and (ov.reflect_plan if kind == "plan" else ov.reflect_design)
    return on and state.get("oversee_calls", 0) < ov.max_calls_milestone


def _contested(critique: Critique, responses: list, failed: bool) -> list[OversightFlag]:
    """Serious items the author rebutted or left unanswered: the owner decides, never autopilot."""
    answered = {r.id: r.verdict for r in responses}
    out = []
    for item in critique.items:
        verdict = "revision failed" if failed else answered.get(item.id, "unanswered")
        if item.severity == "high" and verdict != "changed":
            text = f"Overseer critique {item.id} ({verdict}): {item.text}"
            out.append(OversightFlag(severity="high", text=text[:600], evidence=item.evidence))
    return out


def critique_plan(state: PipelineState, deps: RunDeps) -> Update:
    evidence = collect_evidence(
        state, "plan", max_chars=deps.settings.budgets.overseer.evidence_chars
    )
    critique = run_critique("plan", state["roadmap"].goal, None, evidence, deps.ctx())
    return {"plan_critique": critique, "oversee_calls": state.get("oversee_calls", 0) + 1}


def revise_plan(state: PipelineState, deps: RunDeps) -> Update:
    """D94: the planner answers the critique once; a failed revision keeps the valid draft."""
    draft, critique = state["roadmap"], state["plan_critique"]
    new, responses, failed = draft, [], False
    if critique.items:
        try:
            revised, responses = revise_roadmap(
                state["brief"],
                state.get("plan_base"),
                draft,
                critique,
                deps.ctx(briefing=_briefing(state, "long_planner")),
                proposal=_proposal_text(state, deps),
                benchmarks=_outline(deps),
                known_ref=partial(_known_ref, _reference(deps)),
            )
            new = current_on_open(revised)
        except ContractViolation as e:
            failed = True
            log.warning("roadmap revision failed; draft kept", extra={"error": str(e)[:300]})
    return {
        "roadmap": new,
        "approved_roadmap": _approved(state, deps, new),
        "plan_review": False,
        "plan_base": None,
        "plan_responses": responses,
        "plan_revision_failed": failed,
        "review_flags": _contested(critique, responses, failed),
    }


def short_plan(state: PipelineState, deps: RunDeps) -> Update:
    m = _milestone(state)
    query = f"{m.title}. " + " ".join(c.text for c in m.acceptance_criteria)
    budget = deps.settings.budgets.context_pack_tokens.short_planner
    pack = build_pack(query, budget, deps.settings.budgets.context_pack.k, deps.index, deps.blobs)
    issues = _issues(state, "spec")
    previous = state.get("spec") if issues else None
    try:
        spec = run_short_planner(
            state["roadmap"],
            m,
            pack,
            previous,
            issues,
            deps.ctx(m.id, _briefing(state, "short_planner")),
            proposal=_proposal_text(state, deps),
        )
    except ContractViolation as e:  # the milestone is too big to specify within the limits
        splits = dict(state.get("spec_splits") or {})
        if splits.get(m.id, 0) >= 1:
            raise ContractViolation(
                f"{m.id}: spec still does not fit (split already tried): {str(e)[:300]}"
            ) from None
        splits[m.id] = splits.get(m.id, 0) + 1
        note = (
            f"The spec for {m.id} could not fit the spec limits ({str(e)[:200]}). "
            f"Please split {m.id} into two or three smaller milestones, keeping every "
            "acceptance criterion and the dependency order."
        )
        log.warning("spec too large; asking for a split", extra={"milestone": m.id})
        return {"spec": None, "split_request": True, "spec_splits": splits, "human_note": note}
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


def _reference(deps: RunDeps) -> dict[str, Any]:
    _reference_data(deps)
    return load_reference(deps.paths.root)


def _known_ref(reference: dict[str, Any], ref: str) -> bool:
    try:
        return bool(expected_refs(reference, [ref]))
    except KeyError:
        return False


def _outline(deps: RunDeps) -> str:
    """D93: the benchmark refs the long planner may assign to milestones (no values needed)."""
    return outline(_reference(deps))


def _benchmarks(m: Milestone, deps: RunDeps) -> list[dict[str, str]]:
    """What the designer and coder may know about this milestone's benchmarks: never values."""
    if not m.benchmarks:
        return []
    try:
        return coder_view(_reference(deps), m.benchmarks)
    except KeyError as e:  # a ref the planner made up: shown at the benchmark gate, not fatal
        log.warning("unknown benchmark ref", extra={"milestone": m.id, "error": str(e)[:200]})
        return [{"ref": r} for r in m.benchmarks]


def _existing_files(deps: RunDeps) -> list[str]:
    root = deps.workspace().root
    return sorted(
        p.relative_to(root).as_posix()
        for d in ("src", "bench", "figures")
        for p in (root / d).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def _coordinator_notes(state: PipelineState) -> str | None:
    """D93: a spec still failing review after the revise rounds goes on with its issues noted."""
    v = state.get("verdict")
    if v is None or v.verdict != "revise":
        return None
    issues = "\n".join(f"- [{i.severity}] {i.text}" for i in v.issues)
    return f"The reviewer's unresolved notes on the spec; respect them in the design:\n{issues}"


DESIGN_TRIES = 2


def _design_pack(m: Milestone, deps: RunDeps):
    query = f"{m.title}. " + " ".join(c.text for c in m.acceptance_criteria)
    return build_pack(
        query,
        deps.settings.budgets.context_pack_tokens.math_designer,
        deps.settings.budgets.context_pack.k,
        deps.index,
        deps.blobs,
    )


def _design_failed(state: PipelineState, m: Milestone, e: Exception) -> Update:
    """An unusable design: one retry, then back to the long-term planner with the reason."""
    failed = state.get("design_attempts", 0)
    why = f"The previous answer could not be used: {str(e)[:600]}"
    if failed + 1 < DESIGN_TRIES:
        return {
            "design_ok": False,
            "design_attempts": failed + 1,
            "design_feedback": why,
            "design_review": False,
            "design_draft": None,
        }
    note = f"{m.id}: the math designer failed twice ({str(e)[:300]}). Simplify or split it."
    return {
        "design_ok": False,
        "design_attempts": 0,
        "design_feedback": None,
        "human_note": note,
        "design_review": False,
        "design_draft": None,
    }


def _previous(state: PipelineState, m: Milestone):
    prev = state.get("math_spec")
    return prev if prev is not None and prev.milestone_id == m.id else None


def _commit_design(state: PipelineState, deps: RunDeps, m: Milestone, out: MathDesign) -> Update:
    ws = deps.workspace()
    git = Git(ws)
    written = write_design(ws, out, _previous(state, m))
    stubs = record_stubs(ws.root, written)
    commit = git.commit(f"{m.id}: math design (stubs)")
    git.branch(f"wip/{m.id}", commit)
    return {
        "math_spec": out.spec,
        "stubs": stubs,
        "design_ok": True,
        "design_attempts": 0,
        "design_feedback": None,
        "coder_sessions": 0,
        "test_result": None,
    }


def design(state: PipelineState, deps: RunDeps) -> Update:
    """#4 writes the formulation and stubs (D93). With reflection (D94) the valid draft is kept
    unwritten for the overseer's critique; otherwise it is written and committed here."""
    m = _milestone(state)
    Git(deps.workspace()).checkout("main")
    feedback = state.get("design_feedback") or _coordinator_notes(state)
    try:
        out = run_math_designer(
            state["spec"],
            m,
            _design_pack(m, deps),
            _existing_files(deps),
            feedback,
            deps.ctx(m.id, _briefing(state, "math_designer")),
            benchmarks=_benchmarks(m, deps),
            proposal=_proposal_text(state, deps),
        )
        if _reflect(state, deps, "design"):
            return {"design_draft": out, "design_review": True, "design_ok": True}
        return _commit_design(state, deps, m, out)
    except (ContractViolation, DesignError) as e:
        return _design_failed(state, m, e)


def critique_design(state: PipelineState, deps: RunDeps) -> Update:
    """D94: the overseer reads the unwritten draft: its formulation, algorithm and stub code."""
    m = _milestone(state)
    draft = state["design_draft"]
    files = {f.path: f.content for f in draft.files}
    lim = deps.settings.budgets.overseer
    evidence = collect_evidence(
        {**state, "math_spec": draft.spec}, "design", files, lim.evidence_chars
    )
    critique = run_critique("design", state["roadmap"].goal, m.id, evidence, deps.ctx(m.id))
    return {"design_critique": critique, "oversee_calls": state.get("oversee_calls", 0) + 1}


def finalize_design(state: PipelineState, deps: RunDeps) -> Update:
    """D94: the designer answers the critique once, then the design is written. A failed
    revision keeps the valid draft; a draft that cannot be written is a failed design."""
    m = _milestone(state)
    draft, critique = state["design_draft"], state["design_critique"]
    out, responses, failed = draft, [], False
    if critique.items:
        try:
            out, responses = revise_design(
                state["spec"],
                m,
                _design_pack(m, deps),
                _existing_files(deps),
                deps.ctx(m.id, _briefing(state, "math_designer")),
                draft,
                critique,
                benchmarks=_benchmarks(m, deps),
                proposal=_proposal_text(state, deps),
            )
        except ContractViolation as e:
            failed = True
            log.warning("design revision failed; draft kept", extra={"error": str(e)[:300]})
    review = {
        "design_review": False,
        "design_draft": None,
        "design_responses": responses,
        "design_revision_failed": failed,
        "review_flags": [
            *(state.get("review_flags") or []),
            *_contested(critique, responses, failed),
        ],
    }
    try:
        return review | _commit_design(state, deps, m, out)
    except DesignError as e:
        if out is not draft:  # the revision broke the design: fall back to the valid draft
            try:
                return (
                    review
                    | {"design_revision_failed": True}
                    | _commit_design(state, deps, m, draft)
                )
            except DesignError:
                pass
        return review | _design_failed(state, m, e)


def code(state: PipelineState, deps: RunDeps) -> Update:
    """One coder session; the harness reruns the stub check afterwards (never trusting it)."""
    m = _milestone(state)
    b = deps.settings.budgets
    cfg = deps.settings.models.roles["coder"]
    n = state.get("coder_sessions", 0)
    model = cfg.escalate_to if cfg.escalate_to and n >= b.coder_escalate_after else None
    ws, box = deps.workspace(), deps.sandbox()
    stubs = state.get("stubs") or []
    reproduce = reproduce_script(m.id) if m.benchmarks else None
    timeout = b.sandbox.test_timeout_s
    git = Git(ws)
    git.checkout(f"wip/{m.id}")
    tools = CoderTools(
        ws,
        box,
        run_id=deps.run_id,
        milestone_id=m.id,
        timeout_s=timeout,
        stubs=stubs,
        reproduce=reproduce,
    )
    retry = bool(state.get("verify_retry")) and state.get("verification") is not None
    session = run_coder_session(
        state["spec"],
        state["math_spec"],
        state.get("test_result"),
        tools,
        deps.ctx(m.id, _briefing(state, "coder")),
        model=model,
        benchmarks=_benchmarks(m, deps),
        results_file=default_results(m.id),
        mismatches=render_verification(state["verification"]) if retry else None,
    )
    final = check_code(
        ws, box, stubs, reproduce, timeout_s=timeout, run_id=deps.run_id, milestone_id=m.id
    )
    git.commit(f"{m.id}: coder session {n + 1} ({'pass' if final.passed else 'fail'})")
    if final.passed:
        git.checkout("main")
        git.run("merge", "-q", "--ff-only", f"wip/{m.id}")
    return {
        "coder_sessions": n + 1,
        "coder_message": (session.loop.final_text or "")[:1500] or None,
        "test_result": final,
        "verify_retry": False,
    }


def compare(state: PipelineState, deps: RunDeps) -> Update:
    """D93: compare the owner's results file with the reference. Plain Python decides; a wrong
    value or an incomplete report goes back to the coder at most `max_fix_rounds` times."""
    m = _milestone(state)
    ws = deps.workspace()
    try:
        path = results_file(ws.root, m.id, state.get("benchmark_path") or "")
        rep = check_results(
            path, _reference(deps), m.benchmarks, run_id=deps.run_id, milestone_id=m.id
        )
    except BenchmarkFileError as e:
        return {"benchmark_error": str(e), "verification": None, "verify_retry": False}
    rounds = state.get("fix_rounds", 0)
    retry = rep.status in ("mismatch", "error") and rounds < (
        deps.settings.budgets.autopilot.max_fix_rounds
    )
    log.warning("benchmarks", extra={"status": rep.status, "mismatched": rep.n_mismatched})
    return {
        "verification": rep,
        "benchmark_error": None,
        "benchmark_path": None,
        "verify_retry": retry,
        "fix_rounds": rounds + int(retry),
    }


def render_verification(rep: VerificationReport | None) -> str:
    """One short paragraph for gates and the coder."""
    if rep is None or rep.status == "none":
        return "Benchmarks: none for this milestone"
    if rep.status == "verified":
        return f"Benchmarks: verified ({rep.n_within} of {rep.n_checked} values within tolerance)"
    if rep.status == "unchecked_only":
        return f"Benchmarks: {rep.n_unchecked} values reported, none checkable (no tolerances)"
    lines = []
    if rep.status == "mismatch":
        n, of = rep.n_mismatched, rep.n_checked
        lines.append(f"Benchmarks: mismatch: {n} of {of} values outside tolerance")
    else:
        lines.append("Benchmarks: the results could not be fully checked")
    for mm in rep.mismatches[:8]:
        tol = f" (±{mm.tolerance:g})" if mm.tolerance is not None else " (exact)"
        lines.append(f"- {mm.ref}: generated {mm.generated}, recorded {mm.recorded}{tol}")
    if rep.log_tail:
        lines.append(rep.log_tail[-800:])
    return "\n".join(lines)


def digest(state: PipelineState, deps: RunDeps) -> Update:
    m = _milestone(state)
    check = state["test_result"]
    d = build_digest(
        state["spec"],
        check,
        state.get("verification"),
        deps.ledger,
        run_id=deps.run_id,
        milestone_id=m.id,
    )
    if d.status != "met":
        note = write_deviations(state["spec"], d, check, deps.ctx(m.id))
        d = d.model_validate({**d.model_dump(), "deviations": note[:500]})
    return {"digest": d}


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
    auto = _autopilot(state, deps, req.gate)
    if auto is not None:
        return auto
    ov = deps.settings.budgets.overseer
    md = render_oversight(state, ov.flags_shown, ov.flag_chars)
    if md:
        req = req.model_copy(update={"markdown": f"{req.markdown}\n\n{md}"})
    return HumanDecision.model_validate(interrupt(req.model_dump()))


def _autopilot(state: PipelineState, deps: RunDeps, gate: str) -> HumanDecision | None:
    """Phase 7d (D76): plain-Python decision for routine gates, logged; None asks the human."""
    cfg = deps.settings.budgets.autopilot
    if not cfg.enabled:
        return None
    rm = state.get("roadmap")
    m = next_milestone(rm) if rm is not None else None
    mid = m.id if m is not None else None
    flags = list(state.get("flags") or [])
    res = state.get("test_result")
    facts = Facts(
        run_id=deps.run_id,
        milestone_id=mid,
        flags=[*flags, *(state.get("review_flags") or [])],
        tests_passed=None if res is None else res.passed,
        verification=state.get("verification"),
        first_roadmap=state.get("approved_roadmap") is None,
        has_benchmarks=bool(m is not None and m.benchmarks),
    )
    d = decide_gate(gate, facts, cfg)
    if d is None:
        return None
    entry = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "gate": gate,
        "milestone": mid,
        "action": d.action,
        "note": d.note,
    }
    with (deps.paths.root / DECISIONS_LOG).open("a") as f:
        f.write(json.dumps(entry) + "\n")
    log.warning("autopilot", extra={"gate": gate, "action": d.action, "note": d.note[:200]})
    return d


DECISIONS_LOG = "decisions.jsonl"


SEVERITY = {"high": "serious", "warn": "warning", "info": "note"}
_RANK = {"high": 0, "warn": 1, "info": 2}


def render_oversight(
    state: PipelineState, flags_shown: int, flag_chars: int = 280, full: bool = False
) -> str:
    """The overseer's flags and briefings. At gates (full=False): a one-line count, flags most
    serious first, shortened, without evidence codes, and only the names of briefed agents, so
    the owner can read it quickly. `r2r show <run> oversight` uses full=True."""
    briefings, flags = state.get("briefings") or {}, list(state.get("flags") or [])
    if not briefings and not flags:
        return ""
    newest = list(reversed(flags))[:flags_shown]
    shown = sorted(newest, key=lambda f: _RANK[f.severity])  # stable: newest first per level
    plural = {"high": "serious", "warn": "warnings", "info": "notes"}
    counts = [
        f"{n} {plural[k] if n > 1 else SEVERITY[k]}"
        for k in ("high", "warn", "info")
        if (n := sum(f.severity == k for f in shown))
    ]
    head = "**Overseer:** " + (", ".join(counts) or "no flags") + " (advice only)"
    lines = [head]
    for f in shown:
        mark = "⚑ " if f.severity != "info" else ""
        text = " ".join(f.text.split())
        if full:
            lines.append(f"- {mark}{SEVERITY[f.severity]}: {text} [{', '.join(f.evidence)}]")
            continue
        if len(text) > flag_chars:
            text = text[: flag_chars - 1].rstrip() + "…"
        lines.append(f"- {mark}{SEVERITY[f.severity]}: {text}")
    if briefings and full:
        lines += [f"- briefing for {role}: {text}" for role, text in briefings.items()]
    elif briefings:
        names = ", ".join(r.replace("_", " ") for r in briefings)
        run = state.get("run_id") or "<run>"
        lines.append(f"Briefed: {names} · details: `uv run r2r show {run} oversight`")
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
    if point in ("design", "code"):  # the code itself; never the reference values (D93)
        root = deps.workspace().root
        paths = dict.fromkeys(s["path"] for s in state.get("stubs") or [])
        files = {p: (root / p).read_text() for p in paths if (root / p).is_file()}
    evidence = collect_evidence(state, point, files, lim.evidence_chars)
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
    req = gate_request("roadmap", title, rm)
    review = render_review(
        "Roadmap review",
        state.get("plan_critique"),
        state.get("plan_responses"),
        bool(state.get("plan_revision_failed")),
    )
    if review:
        req = req.model_copy(update={"markdown": req.markdown + "\n\n" + review})
    brief = state.get("brief")
    if brief is not None and brief.benchmarks and any(m.benchmarks for m in rm.milestones):
        req = req.model_copy(
            update={"markdown": req.markdown + "\n\n" + _render_benchmarks(brief.benchmarks)}
        )
    d = _ask(state, deps, req)
    # the flags were about this roadmap: settled by the owner's decision either way (the
    # briefings stay: they guide the planners)
    update: Update = {
        "human_decision": d,
        "flags": [],
        "review_flags": [],
        "plan_critique": None,
        "plan_responses": [],
        "plan_revision_failed": False,
    }
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


def result_gate(state: PipelineState, deps: RunDeps) -> Update:
    digest = state["digest"]
    md = render(digest) + "\n\n" + render_verification(state.get("verification"))
    review = render_review(
        "Design review",
        state.get("design_critique"),
        state.get("design_responses"),
        bool(state.get("design_revision_failed")),
    )
    if review:
        md += "\n\n" + review
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
        # an autopilot approval's note explains itself; only the owner's notes ask for a replan
        "human_note": (d.note or None) if d.created_by == "human" else None,
        "math_spec": None,
        "stubs": [],
        "design_attempts": 0,
        "design_feedback": None,
        "test_result": None,
        "coder_sessions": 0,
        "verification": None,
        "benchmark_path": None,
        "benchmark_error": None,
        "fix_rounds": 0,
        "verify_retry": False,
        "briefings": dict(state.get("carry_briefings") or {}),
        "carry_briefings": {},
        "flags": [],
        "oversee_calls": 0,
        "coder_message": None,
        "design_critique": None,
        "design_responses": [],
        "design_revision_failed": False,
        "review_flags": [],
    }
    if d.action in ("approve", "edit"):
        update |= reset | {
            "roadmap": mark_done(state["roadmap"], digest.milestone_id),
            "cycle": state.get("cycle", 1) + 1,
        }
    else:  # redirect: milestone stays open; the planner decides with the note
        update |= reset
    return update


def coder_gate(state: PipelineState, deps: RunDeps) -> Update:
    r = state["test_result"]
    md = f"{state.get('coder_sessions', 0)} coder sessions used.\n\n" + render(r)
    if state.get("coder_message"):
        md += f"\n\n**The coder's last message**\n\n{state['coder_message']}"
    d = _ask(
        state,
        deps,
        GateRequest(gate="coder", title=f"{r.milestone_id}: code check still failing", markdown=md),
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


def _render_benchmarks(benchmarks) -> str:
    lines = ["**Benchmarks from the proposal and papers** (check the values and quotes)"]
    for b in benchmarks:
        units = f" {b.units}" if b.units else ""
        mark = "" if b.verified else " · ⚑ quote not found in the source"
        lines.append(
            f"- benchmarks.{b.id}: {b.quantity} = {b.value:g}{units} (±{b.tolerance:g}) · "
            f'{b.source}: "{b.quote}"{mark}'
        )
    return "\n".join(lines)


def benchmark_gate(state: PipelineState, deps: RunDeps) -> Update:
    """D93: the owner runs the reproduce script; approve means the results file is ready."""
    m = _milestone(state)
    ws = deps.workspace()
    script = reproduce_script(m.id)
    out = default_results(m.id)
    refs = "\n".join(
        f"- `{b['ref']}`" + (f": {b['quantity']}" if b.get("quantity") else "")
        for b in _benchmarks(m, deps)[:40]
    )
    md = (
        f"## {m.id}: benchmark run needed\n\nThe code passes its check. Run the reproduce script "
        f"yourself (it may take a while), then approve:\n\n"
        f'```\ncd "{ws.root}"\nPYTHONPATH=src python {script}\n```\n\n'
        f"It writes `{out}`. Approve reads that file; to use another file, give its path "
        f"(relative to the workspace or absolute). Python compares the values with the reference "
        f"and tolerances.\n\n**Values to reproduce**\n{refs or '- (none listed)'}"
    )
    if state.get("benchmark_error"):
        md = f"**The last results file could not be used:** {state['benchmark_error']}\n\n" + md
    req = GateRequest(
        gate="benchmark",
        title=f"{m.id}: run the benchmarks",
        markdown=md,
        path_prompt=f"Results file (Enter for {out}): ",
    )
    d = _ask(state, deps, req)
    update: Update = {"human_decision": d}
    if d.action == "stop":
        return update | {"status": "stopped"}
    if d.action == "redirect":
        return update | {"human_note": d.note}
    return update | {"benchmark_path": d.note.strip() or None}
