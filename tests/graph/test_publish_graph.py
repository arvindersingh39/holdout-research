"""Phase 8 in the graph: finish -> publish -> publication gate (approve, one revision, stop)."""

import json
from pathlib import Path

import pytest
from graph_helpers import ROADMAP, FakeChannel, decide, make_deps, script_milestone

import r2r.publish.paper as paper
from r2r.graph.driver import drive, open_graph
from r2r.llm.fake import FakeLLM

SECTIONS = ("abstract", "introduction", "method", "experiments", "conclusion")


def script_writer(fake: FakeLLM, tag: str = "") -> None:
    fake.script("writer", *(json.dumps({"name": n, "latex": f"{n} text{tag}."}) for n in SECTIONS))


@pytest.fixture(autouse=True)
def fake_tex(monkeypatch: pytest.MonkeyPatch) -> None:
    """tectonic exists only in the sandbox image; Docker tests cover the real compile."""

    def compile_(build, sandbox_for, timeout_s, tail_lines):
        (build.root / "paper.pdf").write_bytes(b"%PDF-1.5")
        return True, "Output written on paper.pdf"

    monkeypatch.setattr(paper, "_compile", compile_)


def _run(tmp_path: Path, fake: FakeLLM, ch: FakeChannel):
    deps = make_deps(tmp_path, fake, publish_modes=("paper",))
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    return deps, res


def _finish_two_milestones(fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP, ROADMAP, ROADMAP)
    script_milestone(fake, 1)
    script_milestone(fake, 2)


def test_finished_run_is_published_then_gated(tmp_path: Path, fake: FakeLLM) -> None:
    _finish_two_milestones(fake)
    script_writer(fake)
    ch = FakeChannel(*[decide("approve")] * 5)
    deps, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "tests", "result", "result", "publication"]
    md = ch.requests[-1].markdown
    assert "paper.pdf" in md and "experiments" in md
    assert res.status == "complete"
    (bundle,) = res.state["publication"]
    assert bundle.mode == "paper" and bundle.build_ok and bundle.artifacts
    assert (deps.paths.root / "publish" / "paper" / "paper.pdf").is_file()
    # the writer saw both milestones' digests even though state holds only the last one
    exp = [c for c in fake.calls if c.role == "writer"][3].messages[1]["content"]
    assert "M1 milestone 1" in exp and "M2 milestone 2" in exp


def test_one_revision_with_the_owners_note(tmp_path: Path, fake: FakeLLM) -> None:
    _finish_two_milestones(fake)
    script_writer(fake)
    script_writer(fake, " revised")
    ch = FakeChannel(
        *[decide("approve")] * 4,
        decide("redirect", note="Shorten the abstract."),
        decide("approve"),
    )
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates[-2:] == ["publication", "publication"]
    revised = [c for c in fake.calls if c.role == "writer"][5:]
    assert len(revised) == 5 and all("Shorten the abstract." in str(c.messages) for c in revised)
    assert res.status == "complete"


def test_stop_at_publication(tmp_path: Path, fake: FakeLLM) -> None:
    _finish_two_milestones(fake)
    script_writer(fake)
    ch = FakeChannel(*[decide("approve")] * 4, decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert res.status == "stopped"


def test_cli_publish_on_a_paused_run(
    tmp_path: Path, fake: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`r2r publish` works on a run that isn't finished: the paper says it is partial."""
    from typer.testing import CliRunner

    import r2r.cli as cli
    from r2r.harness.sandbox import LocalProcessBackend

    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    deps = make_deps(tmp_path, fake)  # publishing off inside the graph
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))  # stop at M1's result
    with open_graph(deps) as graph:
        drive(graph, deps.run_id, ch, start=True)
    script_writer(fake)
    monkeypatch.setattr(cli, "make_client", lambda settings: fake)
    monkeypatch.setattr(cli, "make_sandbox_factory", lambda: LocalProcessBackend)
    out = CliRunner().invoke(
        cli.app, ["publish", deps.run_id, "--mode", "paper", "--runs-base", str(tmp_path)]
    )
    assert out.exit_code == 0, out.output
    assert "Partial run" in out.output and "paper.pdf" in out.output
    tex = (deps.paths.root / "publish" / "paper" / "paper.tex").read_text()
    assert "Partial report" in tex
