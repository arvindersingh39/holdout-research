"""#3 Short-term planner: the current milestone to a MilestoneSpec (Phase 3)."""

from __future__ import annotations

from pydantic import BaseModel

from r2r.agents.common import draft_model, finalize
from r2r.contracts import Issue, Milestone, MilestoneSpec, Roadmap
from r2r.llm.gateway import CallContext, call_structured
from r2r.retrieval.context_pack import ContextPack

ROLE = "short_planner"
SpecDraft = draft_model(MilestoneSpec, {"context_refs"})


class ShortPlanInput(BaseModel):
    goal: str
    roadmap_rationale: str
    milestone: Milestone
    context: str  # retrieved chunks, each inside <document> tags
    previous_spec: MilestoneSpec | None = None
    coordinator_issues: list[Issue] = []


def run_short_planner(
    roadmap: Roadmap,
    milestone: Milestone,
    pack: ContextPack,
    previous: MilestoneSpec | None,
    issues: list[Issue],
    ctx: CallContext,
) -> MilestoneSpec:
    payload = ShortPlanInput(
        goal=roadmap.goal,
        roadmap_rationale=roadmap.rationale,
        milestone=milestone,
        context=pack.text,
        previous_spec=previous,
        coordinator_issues=issues,
    )
    draft = call_structured(ROLE, ROLE, payload, SpecDraft, ctx)
    return finalize(
        MilestoneSpec, draft, ROLE, ctx, context_refs=[r.model_dump() for r in pack.refs]
    )
