"""Simplifier (D85) in the graph: baseline → attempts → stop rule → digest with runtime metrics."""

from pathlib import Path

import pytest
from graph_helpers import PASS, ROADMAP, SPEC, FakeChannel, coder_pass, decide, design, make_deps

from r2r.contracts import OptimizationPatch
from r2r.graph import nodes
from r2r.graph.driver import drive, open_graph
from r2r.llm.fake import FakeLLM
from r2r.llm.gateway import BudgetExceeded


def _bad_patch(i: int) -> OptimizationPatch:
    return OptimizationPatch(
        created_by="optimizer",
        run_id="g",
        milestone_id="M1",
        diff=f"--- a/src/sq1/core.py\n+++ b/src/sq1/core.py\n@@ nope {i} @@\n+x\n",
        technique="vectorize",
        rationale=f"idea {i}",
    )


def _script(fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", design(1, bench=True))
    fake.script("coder", *coder_pass(1))


def _run(tmp_path: Path, fake: FakeLLM, *decisions):
    deps = make_deps(tmp_path, fake)
    ch = FakeChannel(*decisions)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    return deps, ch, res


def test_patience_stop_then_digest(tmp_path: Path, fake: FakeLLM) -> None:
    _script(fake)
    fake.script("optimizer", _bad_patch(1), _bad_patch(2))
    _, ch, res = _run(tmp_path, fake, decide("approve"), decide("approve"), decide("stop"))
    assert ch.gates == ["roadmap", "tests", "result"]
    s = res.state
    assert [o.attempt for o in s["opt_outcomes"]] == [1, 2]
    assert not any(o.accepted for o in s["opt_outcomes"])
    assert s["opt_stop_reason"].startswith("patience")
    d = s["digest"]
    assert d.metrics["optimizer_attempts"] == 2
    assert d.metrics["runtime_baseline_s"] > 0
    assert d.metrics["runtime_final_s"] == d.metrics["runtime_baseline_s"]
    assert "Optimizer stopped: patience" in d.deviations
    md = ch.requests[-1].markdown
    assert "Simplifier attempts" in md and "#1 vectorize" in md and "reverted" in md
    assert "complexity" in md
    # the simplifier saw the first attempt's rejection when proposing the second
    second = [r for r in fake.calls if r.role == "optimizer"][1]
    assert "idea 1" in str(second.messages[1]["content"])
    # D85: it reads the package source, not a profile
    assert "### src/sq1/core.py" in str(second.messages[1]["content"])


def test_no_bench_skips_optimizer(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", design(1))  # no benchmark
    fake.script("coder", *coder_pass(1))
    _, _, res = _run(tmp_path, fake, decide("approve"), decide("approve"), decide("stop"))
    assert res.state["opt_stop_reason"] == "no benchmark"
    assert not [r for r in fake.calls if r.role == "optimizer"]
    assert "runtime_baseline_s" not in res.state["digest"].metrics


def test_budget_exhausted_stops_cleanly(
    tmp_path: Path, fake: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script(fake)

    def broke(*a, **kw):
        raise BudgetExceeded("milestone M1: call may cost $0.0100, $0.0010 left")

    monkeypatch.setattr(nodes, "run_optimizer", broke)
    _, ch, res = _run(tmp_path, fake, decide("approve"), decide("approve"), decide("stop"))
    assert res.state["opt_stop_reason"].startswith("budget exhausted")
    assert ch.gates[-1] == "result"


def test_resume_mid_loop_continues_from_next_attempt(tmp_path: Path, fake: FakeLLM) -> None:
    _script(fake)
    fake.script("optimizer", _bad_patch(1), RuntimeError("laptop went to sleep"))
    deps = make_deps(tmp_path, fake)
    ch = FakeChannel(decide("approve"), decide("approve"))
    with open_graph(deps) as graph, pytest.raises(RuntimeError, match="sleep"):
        drive(graph, deps.run_id, ch, start=True)

    fresh = FakeLLM()
    fresh.script("optimizer", _bad_patch(2))
    deps2 = make_deps(tmp_path, fresh, create=False)
    ch2 = FakeChannel(decide("stop"))
    with open_graph(deps2) as graph:
        res = drive(graph, "g", ch2, start=False)
    assert [o.attempt for o in res.state["opt_outcomes"]] == [1, 2]
    assert res.state["opt_stop_reason"].startswith("patience")
    assert ch2.gates == ["result"]


def test_failed_baseline_benchmark_skips_optimizer_not_the_run(
    tmp_path: Path, fake: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pilot-2 M5: the baseline benchmark timed out (600 s) and crashed the whole run."""
    from r2r.harness.bench import BenchFailed

    _script(fake)

    def slow(*a, **kw):
        raise BenchFailed("benchmark timed out after 600 s")

    monkeypatch.setattr(nodes, "baseline", slow)
    _, _, res = _run(tmp_path, fake, decide("approve"), decide("approve"), decide("stop"))
    assert res.state["opt_stop_reason"] == "benchmark failed: benchmark timed out after 600 s"
    assert not [r for r in fake.calls if r.role == "optimizer"]
    assert "benchmark failed" in res.state["digest"].deviations
