"""D95 in the graph and CLI: an approved milestone is exported to the owner's folder; a
redirected one is not; the export folder survives `resume`."""

import json
from pathlib import Path

import pytest
from graph_helpers import (
    BRIEF,
    IMPL,
    PILOT,
    ROADMAP,
    FakeChannel,
    decide,
    make_deps,
    script_milestone,
)
from typer.testing import CliRunner

import r2r.cli as cli
from r2r.graph.driver import drive, open_graph
from r2r.harness.sandbox import LocalProcessBackend
from r2r.llm.fake import FakeLLM


def test_approved_milestone_is_exported_redirected_is_not(tmp_path: Path, fake: FakeLLM) -> None:
    replanned = ROADMAP.model_copy(update={"milestones": ROADMAP.milestones[:1]})  # material
    fake.script("long_planner", ROADMAP, replanned)
    script_milestone(fake, 1)
    deps = make_deps(tmp_path, fake)
    deps.export_dir = tmp_path / "project" / "results"
    ch = FakeChannel(decide("approve"), decide("redirect", note="look again"), decide("stop"))
    with open_graph(deps) as graph:
        drive(graph, deps.run_id, ch, start=True)
    assert ch.gates == ["roadmap", "result", "roadmap"]
    assert not deps.export_dir.exists()  # the result was redirected, not approved

    fake2 = FakeLLM()
    fake2.script("retriever", BRIEF)
    only_m1 = ROADMAP.model_copy(update={"milestones": ROADMAP.milestones[:1]})
    fake2.script("long_planner", only_m1)
    script_milestone(fake2, 1)
    (tmp_path / "b").mkdir()
    deps2 = make_deps(tmp_path / "b", fake2)
    deps2.export_dir = tmp_path / "project2"
    with open_graph(deps2) as graph:
        drive(
            graph,
            deps2.run_id,
            FakeChannel(decide("approve"), decide("approve")),
            start=True,
        )
    folder = deps2.export_dir / "M1_milestone-1"
    assert (folder / "code/src/sq1/core.py").read_text() == IMPL
    assert "# M1 · milestone 1" in (folder / "summary.md").read_text()
    assert (deps2.export_dir / "roadmap.md").is_file()


def test_cli_export_dir_is_stored_and_kept_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = FakeLLM()
    f.script("retriever", BRIEF)
    f.script("long_planner", ROADMAP)
    monkeypatch.setattr(cli, "make_client", lambda settings: f)
    monkeypatch.setattr(cli, "make_sandbox_factory", lambda: LocalProcessBackend)
    out = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--proposal",
            str(PILOT),
            "--run-id",
            "x",
            "--runs-base",
            str(tmp_path),
            "--export-dir",
            str(tmp_path / "results"),
        ],
        input="q\n",
    )
    assert out.exit_code == 0, out.output
    meta = json.loads((tmp_path / "runs/x/run.json").read_text())
    assert meta["export_dir"] == str((tmp_path / "results").resolve())
    from r2r.graph.deps import RunDeps

    deps = RunDeps.load("x", cli.project_settings(), f, tmp_path, Path("prompts"))
    assert deps.export_dir == (tmp_path / "results").resolve()
