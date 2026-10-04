"""Phase 7d in the graph: verification feeds the coder, and autopilot decides routine gates."""

import json
import shutil
from pathlib import Path

from graph_helpers import (
    IMPL,
    PASS,
    PILOT,
    ROADMAP,
    SPEC,
    FakeChannel,
    decide,
    design,
    make_deps,
)

from r2r.contracts import FileSpec
from r2r.graph.driver import drive, open_graph
from r2r.llm.fake import FakeLLM, tool_call

WRONG = (
    "def square(x: float) -> float:\n    return float(int(x) * int(x))\n"  # passes 3, -2; not 2.5
)
VERIFY = """import json, pathlib
from sq{n}.core import square


def main():
    p = pathlib.Path("results/verify/verify_square_m{n}.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({{"entries": [{{"ref": "square_of_2_5", "generated": square(2.5)}}]}}))


if __name__ == "__main__":
    main()
"""


def _proposal(tmp_path: Path) -> Path:
    p = tmp_path / "proposal"
    shutil.copytree(PILOT, p)
    (p / "data").mkdir()
    ref = {"square_of_2_5": 6.25, "tolerances": {"square_of_2_5": 1e-9}}
    (p / "data" / "reference.json").write_text(json.dumps(ref))
    return p


def _design(n: int = 1):
    vf = FileSpec(
        path=f"tests/frozen/verify/verify_square_m{n}.py",
        purpose="test",
        content=VERIFY.format(n=n),
    )
    return design(n, extra=(vf,))


def _session(n: int, impl: str) -> list:
    return [
        [tool_call("create_file", path=f"src/sq{n}/core.py", content=impl)],
        [tool_call("run_tests")],
    ]


def _start(fake: FakeLLM, roadmaps: int = 1) -> None:
    fake.script("long_planner", *[ROADMAP] * roadmaps)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", _design())


def _run(tmp_path, fake, ch, **kw):
    deps = make_deps(tmp_path, fake, proposal=_proposal(tmp_path), **kw)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    return deps, res


def rep(point: str, flags: tuple = ()) -> str:
    return json.dumps({"point": point, "briefings": [], "flags": list(flags)})


def _payloads(fake: FakeLLM, role: str) -> list[str]:
    return [
        str(c.messages[1]["content"]) for c in fake.calls if c.role == role and len(c.messages) == 2
    ]


def test_verification_mismatch_goes_back_to_the_coder(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("coder", *_session(1, WRONG), *_session(1, IMPL))
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "tests", "result"]
    fix = _payloads(fake, "coder")[1]
    assert "square_of_2_5" in fix and "6.25" in fix  # the evidence reached the coder
    d = res.state["digest"]
    assert d.status == "met" and d.metrics["verification_mismatched"] == 0
    assert "Verification: verified (1 of 1 values within tolerance)" in ch.requests[-1].markdown


def test_persistent_mismatch_reaches_the_human_even_on_autopilot(
    tmp_path: Path, fake: FakeLLM
) -> None:
    _start(fake)
    fake.script("coder", *_session(1, WRONG), *_session(1, WRONG), *_session(1, WRONG))
    ch = FakeChannel(decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch, autopilot=True)
    assert ch.gates == ["roadmap", "result"]  # tests gate decided automatically
    assert "mismatch" in ch.requests[-1].markdown and "square_of_2_5" in ch.requests[-1].markdown
    assert res.state["digest"].status != "met"


def test_autopilot_runs_both_milestones_after_the_first_roadmap(
    tmp_path: Path, fake: FakeLLM
) -> None:
    _start(fake, roadmaps=3)
    fake.script("coder", *_session(1, IMPL))
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", _design(2))
    fake.script("coder", *_session(2, IMPL))
    ch = FakeChannel(decide("approve"))
    deps, res = _run(tmp_path, fake, ch, autopilot=True)
    assert ch.gates == ["roadmap"] and res.status == "complete"
    log = [json.loads(x) for x in (deps.paths.root / "decisions.jsonl").read_text().splitlines()]
    assert [(e["gate"], e["action"]) for e in log] == [
        ("tests", "approve"),
        ("result", "approve"),
        ("result", "approve"),
    ]
    assert all(e["note"].startswith("autopilot:") for e in log)


def test_serious_test_flag_triggers_an_automatic_redirect(tmp_path: Path, fake: FakeLLM) -> None:
    serious = {
        "severity": "high",
        "text": "Tests expect errors from working code.",
        "evidence": ["E1"],
    }
    late = {"severity": "high", "text": "Result looks suspicious.", "evidence": ["E1"]}
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", _design(), _design())
    fake.script(
        "overseer",
        rep("plan"),
        rep("tests", (serious,)),
        rep("tests"),
        rep("result", (late,)),
    )
    fake.script("coder", *_session(1, IMPL))
    ch = FakeChannel(decide("approve"), decide("stop"))
    deps, _ = _run(tmp_path, fake, ch, autopilot=True, overseer=True)
    assert ch.gates == ["roadmap", "result"]  # a serious flag on the result goes to the human
    assert "Result looks suspicious." in ch.requests[-1].markdown
    second = _payloads(fake, "math_designer")[1]
    assert "Tests expect errors from working code." in second  # the flag became the redirect note
    log = [json.loads(x) for x in (deps.paths.root / "decisions.jsonl").read_text().splitlines()]
    assert [(e["gate"], e["action"]) for e in log][:2] == [
        ("tests", "redirect"),
        ("tests", "approve"),
    ]


def test_cli_autopilot_flag_and_show_decisions(tmp_path: Path, fake: FakeLLM, monkeypatch) -> None:
    from typer.testing import CliRunner

    import r2r.cli as cli
    from r2r.harness.sandbox import LocalProcessBackend

    fake.script("retriever", __import__("graph_helpers").BRIEF)
    _start(fake, roadmaps=3)
    fake.script("coder", *_session(1, WRONG), *_session(1, IMPL))
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", _design(2))
    fake.script("coder", *_session(2, IMPL))
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
        ["run", "--proposal", str(_proposal(tmp_path)), "--run-id", "ap", "--autopilot", *base],
        input="a\nq\n",
    )
    assert out.exit_code == 0, out.output
    shown = CliRunner().invoke(cli.app, ["show", "ap", "decisions", *base])
    assert shown.exit_code == 0, shown.output
    assert "tests" in shown.output and "approve" in shown.output and "autopilot:" in shown.output
