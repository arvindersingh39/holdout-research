"""Overseer (Phase 7c): reads the run's evidence at fixed hook points and writes briefings.

It advises only. Briefings are added to other agents' next prompts by the gateway; flags are
shown to the owner at the next gate. Code builds the evidence (numbered E1, E2, …), drops any
briefing or flag that cites no known evidence, and caps their size. Routing stays in Python.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from r2r.contracts import Briefing, Critique, OversightReport
from r2r.contracts.oversight import OversightPoint
from r2r.ingest.documents import wrap_document
from r2r.llm.gateway import BudgetExceeded, CallContext, call_structured
from r2r.log import get_logger

ROLE = "overseer"
log = get_logger("r2r.agents.overseer")


class Evidence(BaseModel):
    id: str
    kind: str
    text: str


class OverseerInput(BaseModel):
    point: OversightPoint
    goal: str
    milestone_id: str | None
    evidence: list[Evidence]  # each text wrapped in <document> tags: data, not instructions
    active_briefings: dict[str, str]  # role -> briefing currently in that role's prompt


def collect_evidence(
    state: Mapping[str, Any],
    point: OversightPoint,
    files: Mapping[str, str] | None = None,
    max_chars: int = 3000,
) -> list[Evidence]:
    """What the overseer may cite at this hook point, from state contracts only. Never the
    reference values before the result: its briefings reach the designer and coder (D93)."""
    items: list[tuple[str, str]] = []
    rm, spec = state.get("roadmap"), state.get("spec")
    if point == "plan" and rm is not None:
        for m in rm.milestones:
            items += [
                ("criterion", f"{m.id}.{c.id} ({m.title}): {c.text}") for c in m.acceptance_criteria
            ]
            if m.benchmarks:
                items.append(("benchmarks", f"{m.id} reproduces: {', '.join(m.benchmarks)}"))
    elif spec is not None:
        items += [
            ("criterion", f"{spec.milestone_id}.{c.id}: {c.text}") for c in spec.acceptance_criteria
        ]
        if point == "spec":
            items.append(
                ("spec", spec.model_dump_json(exclude={"context_refs", "acceptance_criteria"}))
            )
    elif rm is not None:
        items += [
            ("criterion", f"{m.id}.{c.id}: {c.text}")
            for m in rm.milestones
            for c in m.acceptance_criteria
        ]
    ms = state.get("math_spec")
    if point in ("design", "code") and ms is not None:
        items.append(
            (
                "math_spec",
                ms.model_dump_json(
                    include={"formulation", "algorithm", "signatures", "tolerances"}
                ),
            )
        )
    if point == "code":
        res = state.get("test_result")
        if res is not None:
            items += [
                ("check_failure", f"{f.test_id}: {f.message}\n{f.tail}") for f in res.failures
            ]
        if state.get("coder_message"):
            items.append(("coder_last_message", state["coder_message"]))
    if point in ("design", "code"):
        items += [("code_file", f"# {p}\n{src}") for p, src in (files or {}).items()]
    if point == "result" and state.get("digest") is not None:
        d = state["digest"]
        items.append(
            ("digest", d.model_dump_json(include={"status", "criteria", "metrics", "deviations"}))
        )
        ver = state.get("verification")
        if ver is not None:
            items.append(
                (
                    "benchmark_comparison",
                    ver.model_dump_json(
                        include={"status", "n_checked", "n_within", "mismatches", "log_tail"}
                    ),
                )
            )
    if state.get("human_note"):
        items.append(("owner_note", state["human_note"]))
    return [Evidence(id=f"E{i}", kind=k, text=t[:max_chars]) for i, (k, t) in enumerate(items, 1)]


def run_overseer(
    point: OversightPoint,
    goal: str,
    milestone_id: str | None,
    evidence: list[Evidence],
    active: dict[str, str],
    ctx: CallContext,
) -> OversightReport:
    """Never blocks the run: anything but a budget stop yields an empty report."""
    lim = ctx.settings.budgets.overseer
    known = {e.id for e in evidence}
    payload = OverseerInput(
        point=point,
        goal=goal,
        milestone_id=milestone_id,
        evidence=[
            e.model_copy(update={"text": wrap_document(e.text, source=f"{e.id} {e.kind}")})
            for e in evidence
        ],
        active_briefings=active,
    )

    def clean(r: OversightReport) -> OversightReport:
        cited = lambda ev: bool(ev) and set(ev) <= known  # noqa: E731
        latest: dict[str, Briefing] = {}
        for b in r.briefings:
            if cited(b.evidence):
                latest.pop(b.role, None)  # newest per role wins, in arrival order
                latest[b.role] = b.model_copy(
                    update={"text": _cap(b.text, lim.briefing_tokens * 4)}
                )
        flags = [f for f in r.flags if cited(f.evidence)]
        return r.model_copy(
            update={
                "point": point,
                "briefings": list(latest.values())[: lim.max_briefings],
                "flags": flags,
            }
        )

    try:
        return call_structured(ROLE, ROLE, payload, OversightReport, ctx, finalize=clean)
    except BudgetExceeded:
        raise
    except Exception as e:  # an advisor failure must never stop the pipeline
        log.warning("overseer failed; no briefing", extra={"point": point, "error": str(e)[:300]})
        return OversightReport(
            created_by=ROLE, run_id=ctx.run_id, milestone_id=milestone_id, point=point
        )


class CritiqueInput(BaseModel):
    point: str  # "plan" or "design"
    goal: str
    milestone_id: str | None
    evidence: list[Evidence]  # each text wrapped in <document> tags: data, not instructions


def run_critique(
    point: str, goal: str, milestone_id: str | None, evidence: list[Evidence], ctx: CallContext
) -> Critique:
    """D94: one critique of a new roadmap or design. Items citing unknown evidence are dropped and
    the rest renumbered C1, C2, … Never blocks the run: anything but a budget stop yields an empty
    critique, and the draft goes on unchanged."""
    known = {e.id for e in evidence}
    payload = CritiqueInput(
        point=point,
        goal=goal,
        milestone_id=milestone_id,
        evidence=[
            e.model_copy(update={"text": wrap_document(e.text, source=f"{e.id} {e.kind}")})
            for e in evidence
        ],
    )

    def clean(c: Critique) -> Critique:
        kept = [i for i in c.items if set(i.evidence) <= known]
        items = [i.model_copy(update={"id": f"C{n}"}) for n, i in enumerate(kept, 1)]
        return c.model_copy(update={"point": point, "items": items})

    try:
        return call_structured(ROLE, "overseer_critique", payload, Critique, ctx, finalize=clean)
    except BudgetExceeded:
        raise
    except Exception as e:  # an advisor failure must never stop the pipeline
        log.warning("critique failed; draft goes on", extra={"point": point, "error": str(e)[:300]})
        return Critique(created_by=ROLE, run_id=ctx.run_id, milestone_id=milestone_id, point=point)


def _cap(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"
