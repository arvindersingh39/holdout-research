"""Compact Markdown renderings of contracts for gates and `r2r show`."""

from __future__ import annotations

from functools import singledispatch

from pydantic import BaseModel

from r2r.contracts import CoordinatorVerdict, MilestoneSpec, ResearchBrief, ResultDigest, Roadmap


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (none)"


def _inline(items: list[str]) -> str:
    return ", ".join(items) or "(none)"


@singledispatch
def render(contract: BaseModel) -> str:
    return f"## {type(contract).__name__}\n\n```json\n{contract.model_dump_json(indent=2)}\n```"


@render.register
def _(b: ResearchBrief) -> str:
    return "\n\n".join(
        [
            "## Research brief",
            f"**Objective.** {b.objective}",
            f"**Hypotheses**\n{_bullets(b.hypotheses)}",
            f"**Success metrics**\n{_bullets(b.success_metrics)}",
            f"**Methods** {_inline(b.methods)} · **Datasets** {_inline(b.datasets)}",
            f"**Constraints**\n{_bullets(b.constraints)}",
            f"**Open questions**\n{_bullets(b.open_questions)}",
            f"Papers: {len(b.paper_cards)}",
        ]
    )


@render.register
def _(r: Roadmap) -> str:
    lines = [f"## Roadmap v{r.version}", f"**Goal.** {r.goal}", ""]
    for m in r.milestones:
        mark = "▶" if m.id == r.current_milestone_id else " "
        deps = f" (depends on {', '.join(m.depends_on)})" if m.depends_on else ""
        lines.append(f"{mark} **{m.id} · {m.title}** [{m.status}]{deps}")
        lines += [f"    - {c.id}: {c.text}" for c in m.acceptance_criteria]
    lines += ["", f"**Rationale.** {r.rationale}"]
    if r.decisions_log:
        lines += ["", "**Decisions**", _bullets(r.decisions_log)]
    return "\n".join(lines)


@render.register
def _(s: MilestoneSpec) -> str:
    return "\n\n".join(
        [
            f"## Milestone spec · {s.milestone_id}",
            f"**Tasks**\n{_bullets(s.tasks)}",
            "**Interfaces**\n" + ("\n".join(f"- `{i}`" for i in s.interfaces) or "- (none)"),
            "**Acceptance criteria**\n"
            + "\n".join(f"- {c.id}: {c.text}" for c in s.acceptance_criteria),
            f"**Risks**\n{_bullets(s.risks)}",
            f"**Rationale.** {s.rationale}",
            f"Context chunks: {len(s.context_refs)}",
        ]
    )


@render.register
def _(v: CoordinatorVerdict) -> str:
    head = f"## Coordinator verdict · {v.milestone_id}: **{v.verdict.upper()}**"
    issues = [f"- [{i.severity}] ({i.target}) {i.text}" for i in v.issues]
    return "\n\n".join([head, "\n".join(issues) or "No issues."])


@render.register
def _(d: ResultDigest) -> str:
    crit = "\n".join(
        f"- {c.id}: {'pass' if c.passed else 'FAIL'} · {c.evidence}" for c in d.criteria
    )
    metrics = "\n".join(f"- {k}: {v:g}" for k, v in d.metrics.items()) or "- (none)"
    return "\n\n".join(
        [
            f"## Result · {d.milestone_id}: **{d.status}**",
            f"**Criteria**\n{crit or '- (none)'}",
            f"**Metrics**\n{metrics}",
            f"**Deviations.** {d.deviations or 'none'}",
            f"Cost ${d.cost_usd:.4f} · {d.tokens:,} tokens · {len(d.artifacts)} artifacts",
        ]
    )
