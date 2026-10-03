"""#2 Long-term planner: rewrites the Roadmap each cycle (Phase 3).

The model rewrites the whole roadmap; code sets the version and keeps finished milestones
finished, so history is never appended and status stays code-owned.
"""

from __future__ import annotations

from pydantic import BaseModel

from r2r.contracts import Issue, ResearchBrief, ResultDigest, Roadmap
from r2r.graph.routing import keep_finished
from r2r.llm.gateway import CallContext, call_structured

ROLE = "long_planner"


class LongPlanInput(BaseModel):
    brief: ResearchBrief
    current_roadmap: Roadmap | None = None
    latest_digest: ResultDigest | None = None
    human_note: str | None = None
    coordinator_issues: list[Issue] = []
    proposal: str = ""  # the owner's full proposal when short, inside <document> tags


def run_long_planner(
    brief: ResearchBrief,
    roadmap: Roadmap | None,
    digest: ResultDigest | None,
    note: str | None,
    issues: list[Issue],
    ctx: CallContext,
    proposal: str = "",
) -> Roadmap:
    payload = LongPlanInput(
        brief=brief,
        current_roadmap=roadmap,
        latest_digest=digest,
        human_note=note or None,
        coordinator_issues=issues,
        proposal=proposal,
    )
    out = call_structured(ROLE, ROLE, payload, Roadmap, ctx)
    version = roadmap.version + 1 if roadmap else 1
    return keep_finished(roadmap, Roadmap.model_validate({**out.model_dump(), "version": version}))
