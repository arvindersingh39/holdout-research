"""D94: one-round reflection. The overseer critiques a new design once; the designer revises,
answering every item; the review is rendered for the owner."""

import json

from samples import math_design, milestone, milestone_spec

from r2r.agents.math_designer import revise_design
from r2r.contracts import Critique, CritiqueItem, CritiqueResponse
from r2r.llm.fake import FakeLLM
from r2r.llm.gateway import CallContext
from r2r.retrieval.context_pack import ContextPack

META = {"created_by", "run_id", "milestone_id"}


def _critique(*ids, point="design"):
    items = [CritiqueItem(id=i, severity="high", text=f"fix {i}", evidence=["E1"]) for i in ids]
    return Critique(
        created_by="overseer", run_id="run-test", milestone_id="M1", point=point, items=items
    )


def _revision(design, *answers) -> str:
    data = design.model_dump(exclude=META)
    data["spec"] = {k: v for k, v in data["spec"].items() if k not in META}
    return json.dumps(
        {"design": data, "responses": [{"id": i, "verdict": v, "text": t} for i, v, t in answers]}
    )


def test_design_revision_answers_every_item(ctx: CallContext, fake: FakeLLM) -> None:
    draft = math_design()
    better = draft.model_copy(
        update={
            "spec": draft.spec.model_copy(update={"algorithm": "Use the closed form; float64."})
        }
    )
    fake.script(
        "math_designer",
        _revision(better),  # nothing answered: one repair
        _revision(better, ("C1", "changed", "float64 everywhere")),
    )
    out, responses = revise_design(
        milestone_spec(),
        milestone(),
        ContextPack(text="", refs=[], tokens=0),
        [],
        ctx,
        draft,
        _critique("C1"),
    )
    assert out.spec.algorithm == "Use the closed form; float64." and out.milestone_id == "M1"
    assert [(r.id, r.verdict) for r in responses] == [("C1", "changed")]
    first = str(fake.calls[0].messages[1]["content"])
    assert "fix C1" in first and "closed form with scipy" in first  # the critique and the draft
    assert "missing" in str(fake.calls[1].messages[-1]) and "C1" in str(fake.calls[1].messages[-1])


def test_review_block_pairs_items_and_responses() -> None:
    from r2r.graph.render import render_review

    md = render_review(
        "Design review",
        _critique("C1", "C2"),
        [CritiqueResponse(id="C1", verdict="rebutted", text="The proposal fixes T at 180.")],
        failed=False,
    )
    assert "**Design review**" in md and "C1 (high): fix C1" in md
    assert "rebutted: The proposal fixes T at 180." in md and "C2" in md and "no answer" in md
    assert "revision failed" in render_review("Roadmap review", _critique("C1"), [], failed=True)
    assert render_review("x", _critique(), [], failed=False) == ""


def test_contested_serious_items_become_flags_for_the_owner() -> None:
    """A serious item the author rebutted, left unanswered, or could not revise is decided by the
    owner (autopilot treats it as a serious flag); warnings and accepted changes are not."""
    from r2r.graph.nodes import _contested

    c = _critique("C1", "C2", "C3")
    c = c.model_copy(
        update={"items": [*c.items[:2], c.items[2].model_copy(update={"severity": "warn"})]}
    )
    answers = [CritiqueResponse(id="C1", verdict="changed", text="done")]
    flags = _contested(c, answers, failed=False)
    assert [f.text for f in flags] == ["Overseer critique C2 (unanswered): fix C2"]
    assert all(f.severity == "high" for f in flags)
    failed = _contested(c, answers, failed=True)
    assert [f.text.split(":")[0] for f in failed] == [
        "Overseer critique C1 (revision failed)",
        "Overseer critique C2 (revision failed)",
    ]
