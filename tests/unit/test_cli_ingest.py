"""`r2r ingest` end to end with FakeLLM injected."""

from pathlib import Path

import pytest
from samples import research_brief
from typer.testing import CliRunner

import r2r.cli as cli
from r2r.llm.fake import FakeLLM

REPO = Path(__file__).resolve().parents[2]


def test_ingest_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLLM()
    fake.script(
        "retriever",
        research_brief().model_dump_json(
            exclude={"created_by", "run_id", "milestone_id", "paper_cards"}
        ),
    )
    monkeypatch.setattr(cli, "make_client", lambda settings: fake)
    result = CliRunner().invoke(
        cli.app,
        [
            "ingest",
            "--proposal",
            str(REPO / "examples" / "pilot_mc_pricer"),
            "--run-id",
            "cli-test",
            "--runs-base",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Variance-reduced Monte Carlo pricing" in result.output
    assert "refs.bib" in result.output
    assert "$" in result.output
    run_dir = tmp_path / "runs" / "cli-test"
    assert (run_dir / "refs.bib").exists()
    assert (run_dir / "ledger.sqlite").exists()
    assert (tmp_path / "runs").is_symlink()


def test_ingest_missing_dir(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app, ["ingest", "--proposal", str(tmp_path / "nope"), "--runs-base", str(tmp_path)]
    )
    assert result.exit_code != 0
