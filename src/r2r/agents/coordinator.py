"""Coordinator: scores a MilestoneSpec against the rubric (Phase 3).

It only judges; what happens next (build, revise, escalate) is decided in plain Python by
`graph.routing.route_after_coordinator`.
"""

from __future__ import annotations

from pydantic import BaseModel

from r2r.contracts import CoordinatorVerdict, Milestone, MilestoneSpec, Roadmap
from r2r.llm.gateway import CallContext, call_structured
from r2r.settings import RubricCriterion

ROLE = "coordinator"


class CoordinatorInput(BaseModel):
    spec: MilestoneSpec
    milestone: Milestone
    dependencies: list[Milestone]
    roadmap_rationale: str
    rubric: list[RubricCriterion]
    usd_cap_milestone: float


def run_coordinator(
    spec: MilestoneSpec, roadmap: Roadmap, milestone: Milestone, ctx: CallContext
) -> CoordinatorVerdict:
    by_id = {m.id: m for m in roadmap.milestones}
    payload = CoordinatorInput(
        spec=spec,
        milestone=milestone,
        dependencies=[by_id[d] for d in milestone.depends_on if d in by_id],
        roadmap_rationale=roadmap.rationale,
        rubric=list(ctx.settings.rubric.criteria),
        usd_cap_milestone=ctx.settings.budgets.usd_cap_milestone,
    )
    return call_structured(ROLE, ROLE, payload, CoordinatorVerdict, ctx)
