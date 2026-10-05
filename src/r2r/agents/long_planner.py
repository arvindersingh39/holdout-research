"""#2 Long-term planner: rewrites the Roadmap each cycle (Phase 3).

The model rewrites the whole roadmap; code sets the version and keeps finished milestones
finished, so history is never appended and status stays code-owned.
"""

from __future__ import annotations

from collections.abc import Callable

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
    benchmarks: str = ""  # D93: refs available to assign to milestones


def run_long_planner(
    brief: ResearchBrief,
    roadmap: Roadmap | None,
    digest: ResultDigest | None,
    note: str | None,
    issues: list[Issue],
    ctx: CallContext,
    proposal: str = "",
    benchmarks: str = "",
    known_ref: Callable[[str], bool] | None = None,
) -> Roadmap:
    payload = LongPlanInput(
        brief=brief,
        current_roadmap=roadmap,
        latest_digest=digest,
        human_note=note or None,
        coordinator_issues=issues,
        proposal=proposal,
        benchmarks=benchmarks,
    )
    limit = ctx.settings.budgets.max_criteria_per_milestone
    finished = {
        m.id for m in (roadmap.milestones if roadmap else []) if m.status in ("done", "skipped")
    }

    def check_size(draft: Roadmap) -> Roadmap:
        for m in draft.milestones:
            n = len(m.acceptance_criteria)
            if m.id not in finished and m.status not in ("done", "skipped") and n > limit:
                raise ValueError(  # one repair with this reason
                    f"{m.id} has {n} acceptance criteria; at most {limit} per unfinished "
                    "milestone. Split it into smaller milestones in dependency order."
                )
            unknown = [r for r in m.benchmarks if known_ref is not None and not known_ref(r)]
            if m.id not in finished and unknown:
                raise ValueError(
                    f"{m.id}: benchmark refs {unknown} are not in the benchmarks list; "
                    "use only refs listed there"
                )
        return draft

    out = call_structured(ROLE, ROLE, payload, Roadmap, ctx, finalize=check_size)
    version = roadmap.version + 1 if roadmap else 1
    return keep_finished(roadmap, Roadmap.model_validate({**out.model_dump(), "version": version}))
