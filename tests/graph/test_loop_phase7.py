"""Phase 7 loop: budget gate, deviations note, roadmap size over cycles, crash + resume."""

from pathlib import Path

import pytest
from graph_helpers import (
    PASS,
    REPO,
    ROADMAP,
    SPEC,
    FakeChannel,
    coder_pass,
    decide,
    design,
    make_deps,
    script_milestone,
)

from r2r.contracts import Milestone, Roadmap
from r2r.graph.driver import drive, open_graph, pending_gate
from r2r.llm.fake import FakeLLM
from r2r.settings import load_settings


def _settings(**budgets):
    s = load_settings(REPO / "config", env_file=None)
    return s.model_copy(update={"budgets": s.budgets.model_copy(update=budgets)})


def test_budget_breach_goes_to_gate_then_retries_after_cap_raised(
    tmp_path: Path, fake: FakeLLM
) -> None:
    # $0.03 lets the retriever through (worst case ≈ $0.021) but not the long planner's worst
    # case (7k output tokens at $5/M ≈ $0.035 plus input).
    fake.script("long_planner", ROADMAP)
    tight = _settings(usd_cap_run=0.03, usd_cap_milestone=0.03)
    deps = make_deps(tmp_path, fake, settings=tight)
    ch = FakeChannel(None)  # quit at the budget gate to edit budgets.yaml
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    assert res.status == "paused" and ch.gates == ["budget"]
    md = ch.requests[0].markdown
    assert "long_plan" in md and "Spend by role" in md and "retriever" in md
    assert fake.pending("long_planner") == 1  # the blocked call was never made

    # Owner raises the cap and resumes in a new process: approve retries the blocked step.
    fresh = FakeLLM()
    fresh.script("long_planner", ROADMAP)
    deps2 = make_deps(tmp_path, fresh, create=False)  # normal caps from budgets.yaml
    ch2 = FakeChannel(decide("approve"), decide("stop"))
    with open_graph(deps2) as graph:
        drive(graph, "g", ch2, start=False)
    assert ch2.gates == ["budget", "roadmap"]
    assert fresh.calls[0].role == "long_planner"


def test_budget_gate_stop_and_redirect(tmp_path: Path, fake: FakeLLM) -> None:
    tight = _settings(usd_cap_run=0.01, usd_cap_milestone=0.01)
    deps = make_deps(tmp_path, fake, settings=tight)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, FakeChannel(decide("stop")), start=True)
    assert res.status == "stopped"


def test_deviations_note_when_a_criterion_fails(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", design(1))
    fake.script("coder", "stuck", "still stuck", "no luck")  # 3 failed sessions → coder gate
    fake.script("digest", '{"text": "AC1 fails: square() still raises NotImplementedError."}')
    ch = FakeChannel(decide("approve"), decide("approve"), decide("approve"), decide("stop"))
    deps = make_deps(tmp_path, fake)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    assert ch.gates == ["roadmap", "tests", "coder", "result"]
    d = res.state["digest"]
    assert d.status == "failed"
    assert d.deviations.startswith("AC1 fails: square() still raises NotImplementedError.")
    assert [r.role for r in fake.calls].count("digest") == 1


def test_no_deviations_call_when_all_criteria_pass(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    deps = make_deps(tmp_path, fake)
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    assert res.state["digest"].status == "met"
    assert "digest" not in [r.role for r in fake.calls]


def test_roadmap_stays_under_cap_over_five_cycles(tmp_path: Path, fake: FakeLLM) -> None:
    """Each cycle the planner returns a roadmap with a full 10-entry decisions log; the roadmap
    is rewritten (never appended), so it stays within its 2k-token cap."""
    ms = [
        Milestone(
            id=f"M{i}",
            title=f"milestone {i}",
            acceptance_criteria=[{"id": "AC1", "text": "criterion " + "x" * 120}],
        ).model_dump()
        for i in range(1, 6)
    ]
    big = Roadmap.model_validate(
        {
            **ROADMAP.model_dump(),
            "milestones": ms,
            "decisions_log": [f"decision {i}: " + "y" * 150 for i in range(10)],
            "rationale": "z" * 780,
        }
    )
    fake.script("long_planner", *[big] * 6)
    for n in range(1, 6):
        script_milestone(fake, n)
    deps = make_deps(tmp_path, fake)
    ch = FakeChannel(*[decide("approve")] * 7)  # roadmap, tests, 5 results
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    assert res.status == "complete"
    rm = res.state["roadmap"]
    assert rm.version == 6 and len(rm.decisions_log) <= 10
    assert rm.token_estimate() <= 2000


def test_crash_between_nodes_then_resume(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", RuntimeError("power cut"))
    deps = make_deps(tmp_path, fake)
    with open_graph(deps) as graph, pytest.raises(RuntimeError, match="power cut"):
        drive(graph, deps.run_id, FakeChannel(decide("approve")), start=True)
    fresh = FakeLLM()
    fresh.script("short_planner", SPEC)
    fresh.script("coordinator", PASS)
    fresh.script("math_designer", design(1))
    fresh.script("coder", *coder_pass(1))
    deps2 = make_deps(tmp_path, fresh, create=False)
    with open_graph(deps2) as graph:
        assert pending_gate(graph, "g") is None  # crashed mid-way, not at a gate
        res = drive(graph, "g", FakeChannel(decide("approve"), decide("stop")), start=False)
    assert res.state["digest"].status == "met"
    assert fresh.calls[0].role == "short_planner"
