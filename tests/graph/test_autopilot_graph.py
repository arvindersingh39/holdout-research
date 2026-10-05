"""Phase 7d / D93 in the graph: autopilot decides routine gates; the benchmark gate, a stuck
coder and serious flags still reach the owner."""

import json
from pathlib import Path

from graph_helpers import (
    PASS,
    ROADMAP,
    SPEC,
    FakeChannel,
    coder_pass,
    decide,
    design,
    make_deps,
    script_milestone,
)

from r2r.graph.driver import drive, open_graph
from r2r.llm.fake import FakeLLM


def _run(tmp_path, fake, ch, **kw):
    deps = make_deps(tmp_path, fake, **kw)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    return deps, res


def rep(point: str, flags: tuple = ()) -> str:
    return json.dumps({"point": point, "briefings": [], "flags": list(flags)})


def _log(deps) -> list[tuple[str, str]]:
    lines = (deps.paths.root / "decisions.jsonl").read_text().splitlines()
    return [(e["gate"], e["action"]) for e in map(json.loads, lines)]


def test_autopilot_runs_both_milestones_after_the_first_roadmap(
    tmp_path: Path, fake: FakeLLM
) -> None:
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    script_milestone(fake, 2)
    ch = FakeChannel(decide("approve"))
    deps, res = _run(tmp_path, fake, ch, autopilot=True)
    assert ch.gates == ["roadmap"] and res.status == "complete"
    assert _log(deps) == [("result", "approve"), ("result", "approve")]
    notes = [
        json.loads(x)["note"]
        for x in (deps.paths.root / "decisions.jsonl").read_text().splitlines()
    ]
    assert all(n.startswith("autopilot:") and "no benchmarks" in n for n in notes)


def test_benchmark_gate_waits_for_the_owner_even_on_autopilot(
    tmp_path: Path, fake: FakeLLM
) -> None:
    rm = ROADMAP.model_copy(
        update={
            "milestones": [
                ROADMAP.milestones[0].model_copy(update={"benchmarks": ["benchmarks.BM1"]})
            ]
        }
    )
    fake.script("long_planner", rm)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", design(1))
    from graph_helpers import reproduce

    fake.script("coder", coder_pass(1)[0], *reproduce(1, {"benchmarks.BM1": 1.0}), coder_pass(1)[1])
    deps = make_deps(tmp_path, fake, autopilot=True)
    from r2r.contracts import Benchmark
    from r2r.harness.benchmarks import write_benchmarks

    bm = Benchmark(id="BM1", quantity="q", value=1.0, tolerance=0.1, source="proposal", quote="1.0")
    write_benchmarks(deps.paths.root, [bm])
    ch = FakeChannel(decide("approve"), None)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    assert ch.gates == ["roadmap", "benchmark"] and res.status == "paused"


def test_serious_result_flag_goes_to_the_owner(tmp_path: Path, fake: FakeLLM) -> None:
    late = {"severity": "high", "text": "Result looks suspicious.", "evidence": ["E1"]}
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    fake.script("overseer", rep("plan"), rep("design"), rep("result", (late,)))
    ch = FakeChannel(decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch, autopilot=True, overseer=True)
    assert ch.gates == ["roadmap", "result"]
    assert "Result looks suspicious." in ch.requests[-1].markdown


def test_stuck_coder_goes_to_the_owner_on_autopilot(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", design(1))
    fake.script("coder", *"abcdef")
    ch = FakeChannel(decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch, autopilot=True)
    assert ch.gates == ["roadmap", "coder"]


def test_cli_autopilot_flag_and_show_decisions(tmp_path: Path, fake: FakeLLM, monkeypatch) -> None:
    from typer.testing import CliRunner

    import r2r.cli as cli
    from r2r.harness.sandbox import LocalProcessBackend

    fake.script("retriever", __import__("graph_helpers").BRIEF)
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    script_milestone(fake, 2)
    monkeypatch.setattr(cli, "make_client", lambda settings: fake)
    monkeypatch.setattr(cli, "make_sandbox_factory", lambda: LocalProcessBackend)
    real = cli.project_settings

    def no_publish():  # publishing has its own tests; this one is about autopilot
        st = real()
        b = st.budgets
        pub = b.publish.model_copy(update={"modes": []})
        return st.model_copy(update={"budgets": b.model_copy(update={"publish": pub})})

    monkeypatch.setattr(cli, "project_settings", no_publish)
    base = ["--runs-base", str(tmp_path)]
    out = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--proposal",
            str(__import__("graph_helpers").PILOT),
            "--run-id",
            "ap",
            "--autopilot",
            *base,
        ],
        input="a\n",
    )
    assert out.exit_code == 0, out.output
    shown = CliRunner().invoke(cli.app, ["show", "ap", "decisions", *base])
    assert shown.exit_code == 0, shown.output
    assert "result" in shown.output and "approve" in shown.output and "autopilot:" in shown.output
