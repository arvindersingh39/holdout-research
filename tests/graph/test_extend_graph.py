"""`r2r extend`: a finished run takes a new proposal and grows new milestones on the same
workspace; finished milestones and their code stay as they are."""

import json
from pathlib import Path

import pytest
from graph_helpers import (
    BRIEF,
    ROADMAP,
    FakeChannel,
    decide,
    make_deps,
    script_milestone,
)

from r2r.contracts import Roadmap
from r2r.graph.driver import drive, open_graph
from r2r.graph.extend import ExtendError, extend
from r2r.llm.fake import FakeLLM

ONLY_M1 = Roadmap.model_validate({**ROADMAP.model_dump(), "milestones": [ROADMAP.milestones[0]]})


def _new_proposal(tmp_path: Path) -> Path:
    d = tmp_path / "proposal_rl"
    d.mkdir()
    (d / "proposal.md").write_text("# RL extension\n\nAdd milestone M2: cube the squares.\n")
    return d


def _finish_m1(tmp_path: Path, fake: FakeLLM):
    fake.script("long_planner", ONLY_M1)
    script_milestone(fake, 1)
    deps = make_deps(tmp_path, fake)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, FakeChannel(*[decide("approve")] * 2), start=True)
    assert res.status == "complete"
    return deps


def test_extend_adds_milestones_after_the_finished_ones(tmp_path: Path, fake: FakeLLM) -> None:
    deps = _finish_m1(tmp_path, fake)
    new = _new_proposal(tmp_path)
    fake.script("retriever", BRIEF)
    fake.script("long_planner", ROADMAP)  # M1 (done) + M2 (new)
    script_milestone(fake, 2)
    deps2 = extend(deps, new)
    assert json.loads((deps.paths.root / "run.json").read_text())["proposal_dir"] == str(
        new.resolve()
    )
    ch = FakeChannel(decide("approve"), decide("approve"), decide("approve"))
    with open_graph(deps2) as graph:
        res = drive(graph, deps2.run_id, ch, start=False)
    assert res.status == "complete"
    assert ch.gates == ["roadmap", "result"]  # the new roadmap is shown
    assert [m.status for m in res.state["roadmap"].milestones] == ["done", "done"]
    planner = [r for r in fake.calls if r.role == "long_planner"][-1]
    payload = str(planner.messages[1]["content"])
    assert "cube the squares" in payload  # the new proposal reaches the planner
    assert "Keep every finished milestone" in payload
    root = deps2.workspace().root
    assert (root / "src/sq1/core.py").is_file() and (root / "src/sq2/core.py").is_file()


def test_extend_refuses_a_run_with_open_milestones(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    deps = make_deps(tmp_path, fake)
    with open_graph(deps) as graph:  # stop at M1's result gate: M2 still open
        drive(graph, deps.run_id, FakeChannel(decide("approve"), None), start=True)
    with pytest.raises(ExtendError, match="still open"):
        extend(deps, _new_proposal(tmp_path))


def test_extend_needs_a_proposal_md(tmp_path: Path, fake: FakeLLM) -> None:
    deps = _finish_m1(tmp_path, fake)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ExtendError, match=r"proposal\.md"):
        extend(deps, empty)


def test_cli_extend_reports_problems(tmp_path: Path, fake: FakeLLM, monkeypatch) -> None:
    from typer.testing import CliRunner

    import r2r.cli as cli

    deps = _finish_m1(tmp_path, fake)
    monkeypatch.setattr(cli, "make_client", lambda settings: fake)
    base = ["--runs-base", str(tmp_path)]
    missing = CliRunner().invoke(
        cli.app, ["extend", deps.run_id, "--proposal", str(tmp_path), *base]
    )
    assert missing.exit_code == 2 and "proposal.md not found" in missing.output
