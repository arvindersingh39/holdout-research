"""`r2r run`, `show`, `status` and `resume` through the real terminal gate, with typed input."""

from pathlib import Path

import pytest
from graph_helpers import BRIEF, PILOT, ROADMAP, script_milestone
from typer.testing import CliRunner

import r2r.cli as cli
from r2r.harness.sandbox import LocalProcessBackend
from r2r.llm.fake import FakeLLM


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    f = FakeLLM()
    f.script("retriever", BRIEF)
    f.script("long_planner", ROADMAP)
    script_milestone(f, 1)
    monkeypatch.setattr(cli, "make_client", lambda settings: f)
    monkeypatch.setattr(cli, "make_sandbox_factory", lambda: LocalProcessBackend)
    return f


def _invoke(args: list[str], tmp_path: Path, keys: str = "") -> str:
    result = CliRunner().invoke(cli.app, [*args, "--runs-base", str(tmp_path)], input=keys)
    assert result.exit_code == 0, result.output
    return result.output


def test_run_quit_resume_show_status(tmp_path: Path, fake: FakeLLM) -> None:
    out = _invoke(["run", "--proposal", str(PILOT), "--run-id", "c"], tmp_path, keys="q\n")
    assert "Approve the first roadmap" in out
    assert "paused" in out

    status = _invoke(["status", "c"], tmp_path)
    assert "Waiting at gate: roadmap" in status
    assert "Roadmap v1: 0/2 milestones done" in status

    assert "Roadmap v1" in _invoke(["show", "c", "roadmap"], tmp_path)
    assert "Research brief" in _invoke(["show", "c", "brief"], tmp_path)

    # approve the roadmap and the tests, then stop at the first result gate
    out = _invoke(["resume", "c"], tmp_path, keys="a\na\ns\nenough for today\n")
    assert "Review M1's tests before coding" in out
    assert "Review the result of M1" in out
    assert "stopped" in out
    assert "Milestone spec · M1" in _invoke(["show", "c", "spec"], tmp_path)


def test_show_rejects_unknown_contract(tmp_path: Path, fake: FakeLLM) -> None:
    result = CliRunner().invoke(cli.app, ["show", "c", "nonsense", "--runs-base", str(tmp_path)])
    assert result.exit_code == 2


def test_resume_unknown_run(tmp_path: Path, fake: FakeLLM) -> None:
    result = CliRunner().invoke(cli.app, ["resume", "nope", "--runs-base", str(tmp_path)])
    assert result.exit_code == 2
    assert "no run" in result.output
