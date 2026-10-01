"""Loop decisions are plain Python: routing after the coordinator, gates and the planner."""

from hypothesis import given
from hypothesis import strategies as st
from samples import coordinator_verdict, milestone, roadmap

from r2r.contracts import CoordinatorVerdict, HumanDecision, Issue, Roadmap
from r2r.graph.routing import (
    END,
    is_material_change,
    mark_done,
    next_milestone,
    route_after_coordinator,
    route_after_gate,
)


def _rm(**update) -> Roadmap:
    return Roadmap.model_validate({**roadmap().model_dump(), **update})


def _decision(action: str, **kw) -> HumanDecision:
    return HumanDecision(created_by="human", run_id="run-test", action=action, **kw)


def test_next_milestone_respects_status_and_dependencies() -> None:
    rm = roadmap()
    assert next_milestone(rm).id == "M1"
    done1 = mark_done(rm, "M1")
    assert next_milestone(done1).id == "M2"
    assert next_milestone(mark_done(done1, "M2")) is None


def test_next_milestone_skips_blocked() -> None:
    rm = _rm(current_milestone_id=None,
             milestones=[milestone(1, ["M2"]).model_dump(), milestone(2).model_dump()])  # fmt: skip
    assert next_milestone(rm).id == "M2"


def test_mark_done_keeps_contract_valid() -> None:
    rm = mark_done(roadmap(), "M1")
    assert [m.status for m in rm.milestones] == ["done", "pending"]
    assert Roadmap.model_validate(rm.model_dump()) == rm


def test_material_change() -> None:
    rm = roadmap()
    assert is_material_change(None, rm)
    assert not is_material_change(rm, _rm(rationale="reworded", version=2))
    assert not is_material_change(rm, mark_done(rm, "M1"))
    assert is_material_change(rm, _rm(goal="A different goal."))
    swapped = _rm(milestones=[milestone(2).model_dump(), milestone(1).model_dump()])
    assert is_material_change(rm, swapped)
    assert is_material_change(rm, _rm(milestones=[milestone(1).model_dump()]))


@given(st.permutations([1, 2, 3, 4]))
def test_any_reorder_is_material(order: list[int]) -> None:
    base = _rm(milestones=[milestone(i).model_dump() for i in [1, 2, 3, 4]])
    other = _rm(milestones=[milestone(i).model_dump() for i in order])
    assert is_material_change(base, other) == (order != [1, 2, 3, 4])


def test_route_after_coordinator() -> None:
    passed = CoordinatorVerdict.model_validate(
        {**coordinator_verdict().model_dump(), "verdict": "pass", "issues": []}
    )
    revise = coordinator_verdict()  # one issue targeting the spec
    on_roadmap = CoordinatorVerdict.model_validate({
        **revise.model_dump(),
        "issues": [Issue(target="roadmap", severity="high", text="M1 is too big.").model_dump()],
    })  # fmt: skip
    assert route_after_coordinator(passed, rounds=0, max_rounds=2) == "build"
    assert route_after_coordinator(passed, rounds=2, max_rounds=2) == "build"
    assert route_after_coordinator(revise, rounds=1, max_rounds=2) == "short_plan"
    assert route_after_coordinator(on_roadmap, rounds=1, max_rounds=2) == "long_plan"
    assert route_after_coordinator(revise, rounds=3, max_rounds=2) == "escalation_gate"


def test_route_after_gate() -> None:
    assert route_after_gate("roadmap", _decision("approve")) == "short_plan"
    assert route_after_gate("roadmap", _decision("edit", edited=roadmap())) == "short_plan"
    assert route_after_gate("roadmap", _decision("redirect", note="split M1")) == "long_plan"
    assert route_after_gate("escalation", _decision("approve")) == "build"
    assert route_after_gate("escalation", _decision("redirect", note="x")) == "long_plan"
    assert route_after_gate("result", _decision("approve")) == "long_plan"
    assert route_after_gate("result", _decision("redirect", note="x")) == "long_plan"
    for gate in ("roadmap", "escalation", "result"):
        assert route_after_gate(gate, _decision("stop")) == END
