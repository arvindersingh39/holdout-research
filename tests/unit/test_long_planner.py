"""Owner rule (study-3): at most `max_criteria_per_milestone` (5) acceptance criteria on any
unfinished milestone; bigger roadmaps get a repair with the reason. Finished ones are exempt."""

from samples import criterion, research_brief, roadmap

from r2r.agents.long_planner import run_long_planner
from r2r.contracts import Roadmap
from r2r.llm.fake import FakeLLM
from r2r.llm.gateway import CallContext


def _with_criteria(rm: Roadmap, i: int, n: int, status: str = "pending") -> Roadmap:
    ms = list(rm.milestones)
    ms[i] = ms[i].model_copy(
        update={"acceptance_criteria": [criterion(k) for k in range(1, n + 1)], "status": status}
    )
    return rm.model_copy(update={"milestones": ms})


def test_oversized_milestone_is_sent_back_once(ctx: CallContext, fake: FakeLLM) -> None:
    big = _with_criteria(roadmap(), 1, 11)
    fake.script("long_planner", big, roadmap())
    out = run_long_planner(research_brief(), None, None, None, [], ctx)
    assert all(len(m.acceptance_criteria) <= 5 for m in out.milestones)
    repair = str(fake.calls[1].messages)
    assert "M2 has 11 acceptance criteria" in repair and "at most 5" in repair


def test_finished_milestones_are_exempt(ctx: CallContext, fake: FakeLLM) -> None:
    done_big = _with_criteria(roadmap(), 0, 8, status="done")
    fake.script("long_planner", done_big)
    out = run_long_planner(research_brief(), done_big, None, None, [], ctx)
    assert len(out.milestones[0].acceptance_criteria) == 8 and len(fake.calls) == 1
