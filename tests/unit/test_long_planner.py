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


# --- D94: one revision answering the overseer's critique ----------------------------------------


def _critique(*ids):
    from r2r.contracts import Critique, CritiqueItem

    return Critique(
        created_by="overseer",
        run_id="run-test",
        point="plan",
        items=[CritiqueItem(id=i, severity="high", text=f"fix {i}", evidence=["E1"]) for i in ids],
    )


def _revision(rm: Roadmap, *answers) -> str:
    import json

    data = rm.model_dump(exclude={"created_by", "run_id", "milestone_id"})
    responses = [{"id": i, "verdict": v, "text": t} for i, v, t in answers]
    return json.dumps({"roadmap": data, "responses": responses})


def test_revision_answers_every_item_and_keeps_the_draft_version(
    ctx: CallContext, fake: FakeLLM
) -> None:
    from r2r.agents.long_planner import revise_roadmap

    draft = roadmap().model_copy(update={"version": 3})
    revised = roadmap().model_copy(update={"goal": "Revised goal after the critique."})
    fake.script(
        "long_planner",
        _revision(revised, ("C1", "changed", "split M1")),  # C2 unanswered: one repair
        _revision(revised, ("C1", "changed", "split M1"), ("C2", "rebutted", "M2 is needed")),
    )
    out, responses = revise_roadmap(research_brief(), None, draft, _critique("C1", "C2"), ctx)
    assert out.goal == "Revised goal after the critique." and out.version == 3
    assert [(r.id, r.verdict) for r in responses] == [("C1", "changed"), ("C2", "rebutted")]
    payload = str(fake.calls[0].messages[1]["content"])
    assert "fix C1" in payload and "Price European options" in payload  # critique + draft
    assert "answer every critique item" in str(fake.calls[1].messages) and "C2" in str(
        fake.calls[1].messages[-1]
    )
