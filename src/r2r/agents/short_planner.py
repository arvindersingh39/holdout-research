"""#3 Short-term planner: the current milestone to a MilestoneSpec (Phase 3)."""

from __future__ import annotations

import json

from pydantic import BaseModel

from r2r.agents.common import draft_model, finalize
from r2r.contracts import Issue, Milestone, MilestoneSpec, Roadmap, get_contract_caps
from r2r.llm.gateway import CallContext, call_structured
from r2r.llm.tokens import estimate_tokens
from r2r.retrieval.context_pack import ContextPack

ROLE = "short_planner"
SpecDraft = draft_model(MilestoneSpec, {"context_refs"})
# The spec's cap includes the context refs code attaches, so keep them compact (pilot-1, M3).
MAX_REFS = 5
REF_SUMMARY_CHARS = 100
META_TOKENS = 60  # created_by, run_id, milestone_id


class ShortPlanInput(BaseModel):
    goal: str
    roadmap_rationale: str
    milestone: Milestone
    context: str  # retrieved chunks, each inside <document> tags
    previous_spec: MilestoneSpec | None = None
    coordinator_issues: list[Issue] = []
    max_spec_tokens: int  # the whole spec must fit in this (about 4 characters per token)


def run_short_planner(
    roadmap: Roadmap,
    milestone: Milestone,
    pack: ContextPack,
    previous: MilestoneSpec | None,
    issues: list[Issue],
    ctx: CallContext,
) -> MilestoneSpec:
    refs = [
        r.model_copy(update={"summary": r.summary[:REF_SUMMARY_CHARS]}).model_dump()
        for r in pack.refs[:MAX_REFS]
    ]
    budget = get_contract_caps()["MilestoneSpec"] - estimate_tokens(json.dumps(refs)) - META_TOKENS
    payload = ShortPlanInput(
        goal=roadmap.goal,
        roadmap_rationale=roadmap.rationale,
        milestone=milestone,
        context=pack.text,
        previous_spec=previous,
        coordinator_issues=issues,
        max_spec_tokens=budget,
    )
    return call_structured(
        ROLE,
        ROLE,
        payload,
        SpecDraft,
        ctx,
        finalize=lambda draft: finalize(MilestoneSpec, draft, ROLE, ctx, context_refs=refs),
    )
