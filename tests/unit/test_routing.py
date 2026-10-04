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
    rm = _rm(
        current_milestone_id=None,
        milestones=[milestone(1, ["M2"]).model_dump(), milestone(2).model_dump()],
    )
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
    on_roadmap = CoordinatorVerdict.model_validate(
        {
            **revise.model_dump(),
            "issues": [
                Issue(target="roadmap", severity="high", text="M1 is too big.").model_dump()
            ],
        }
    )
    assert route_after_coordinator(passed, rounds=0, max_rounds=2) == "scout"
    assert route_after_coordinator(passed, rounds=2, max_rounds=2) == "scout"
    assert route_after_coordinator(revise, rounds=1, max_rounds=2) == "short_plan"
    assert route_after_coordinator(on_roadmap, rounds=1, max_rounds=2) == "long_plan"
    assert route_after_coordinator(revise, rounds=3, max_rounds=2) == "escalation_gate"


def test_route_after_gate() -> None:
    assert route_after_gate("roadmap", _decision("approve")) == "short_plan"
    assert route_after_gate("roadmap", _decision("edit", edited=roadmap())) == "short_plan"
    assert route_after_gate("roadmap", _decision("redirect", note="split M1")) == "long_plan"
    assert route_after_gate("escalation", _decision("approve")) == "scout"
    assert route_after_gate("escalation", _decision("redirect", note="x")) == "long_plan"
    assert route_after_gate("result", _decision("approve")) == "long_plan"
    assert route_after_gate("result", _decision("redirect", note="x")) == "long_plan"
    assert route_after_gate("tests", _decision("approve")) == "freeze"
    assert route_after_gate("tests", _decision("redirect", note="x")) == "design"
    assert route_after_gate("design", _decision("approve")) == "freeze"
    assert route_after_gate("design", _decision("redirect", note="x")) == "long_plan"
    assert route_after_gate("coder", _decision("approve")) == "digest"
    assert route_after_gate("tamper", _decision("approve")) == "code"
    for gate in (
        "roadmap",
        "escalation",
        "tests",
        "design",
        "coder",
        "tamper",
        "dispute",
        "result",
    ):
        assert route_after_gate(gate, _decision("stop")) == END


def test_route_after_design_and_code() -> None:
    from r2r.graph.routing import needs_test_review, route_after_code, route_after_design

    assert route_after_design(True, 0, review=True) == "tests_gate"
    assert route_after_design(True, 1, review=False) == "freeze"
    assert route_after_design(False, 1, review=True) == "design"  # one automatic redesign
    assert route_after_design(False, 2, review=True) == "design_gate"
    assert route_after_code(True, True, 1, 3) == "tamper_gate"
    assert route_after_code(False, True, 1, 3) == "opt_baseline"
    assert route_after_code(False, False, 2, 3) == "code"
    assert route_after_code(False, False, 3, 3) == "coder_gate"
    assert route_after_code(False, False, 1, 3, disputed=True) == "dispute_gate"
    assert route_after_gate("dispute", _decision("approve")) == "revise_test"
    assert route_after_gate("dispute", _decision("redirect", note="x")) == "long_plan"
    from r2r.graph.routing import route_after_optimize

    assert route_after_optimize(True, None) == "tamper_gate"
    assert route_after_optimize(False, "patience") == "digest"
    assert route_after_optimize(False, None) == "optimize"
    rm = roadmap()
    assert needs_test_review("first_milestone", rm)
    assert not needs_test_review("first_milestone", mark_done(rm, "M1"))
    assert needs_test_review("always", mark_done(rm, "M1"))
    assert not needs_test_review("never", rm)


# --- leak 3: a met result with nothing to change advances without a replan ---------------------


def _met(status: str = "met", mid: str = "M1"):
    from samples import result_digest

    return result_digest().model_copy(update={"status": status, "milestone_id": mid})


def test_met_result_advances_to_next_milestone_without_replan() -> None:
    from r2r.graph.routing import advance_without_replan

    rm = mark_done(roadmap(), "M1")
    out = advance_without_replan(rm, _met(), note=None, issues=[])
    assert out is not None
    assert out.current_milestone_id == "M2" and out.version == rm.version
    assert [m.status for m in out.milestones] == ["done", "pending"]


def test_last_milestone_met_needs_no_replan() -> None:
    from r2r.graph.routing import advance_without_replan

    rm = mark_done(mark_done(roadmap(), "M1"), "M2").model_copy(
        update={"current_milestone_id": "M2"}
    )
    out = advance_without_replan(rm, _met(mid="M2"), note=None, issues=[])
    assert out is not None and next_milestone(out) is None


def test_replan_when_anything_needs_the_planner() -> None:
    from r2r.graph.routing import advance_without_replan

    done = mark_done(roadmap(), "M1")
    issue = Issue(target="roadmap", severity="high", text="M2 is too big")
    assert advance_without_replan(done, _met("partial"), None, []) is None  # not met
    assert advance_without_replan(done, _met(), "add a figure", []) is None  # owner's note
    assert advance_without_replan(done, _met(), None, [issue]) is None  # coordinator issue
    assert advance_without_replan(roadmap(), _met(), None, []) is None  # result redirected
    assert advance_without_replan(None, None, None, []) is None  # first roadmap
    moved = done.model_copy(update={"current_milestone_id": "M2"})
    assert advance_without_replan(moved, _met(), None, []) is None  # already advanced


# --- D86: the final verifier pass runs only if the simplifier changed the code -----------------


def test_final_verify_only_after_an_accepted_simplification() -> None:
    from r2r.contracts import OptimizationOutcome
    from r2r.graph.build import _via_verify

    def outcome(accepted: bool) -> OptimizationOutcome:
        return OptimizationOutcome(
            created_by="harness",
            run_id="r",
            milestone_id="M1",
            attempt=1,
            accepted=accepted,
            reason="x",
            runtime_before_s=1.0,
            sloc_before=10,
        )

    to_digest = _via_verify(lambda s: "digest", enabled=True)
    assert to_digest({"opt_outcomes": []}) == "digest"
    assert to_digest({"opt_outcomes": [outcome(False)]}) == "digest"
    assert to_digest({"opt_outcomes": [outcome(False), outcome(True)]}) == "verify_final"
    assert _via_verify(lambda s: "opt_baseline", enabled=True)({}) == "verify_code"
    assert (
        _via_verify(lambda s: "digest", enabled=False)({"opt_outcomes": [outcome(True)]})
        == "digest"
    )
