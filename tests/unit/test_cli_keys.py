"""A new user without API keys gets a clear message, not a provider's authentication error."""

import pytest
import typer
from pydantic import SecretStr

from r2r import cli
from r2r.settings import Secrets


def _settings(**keys: str):
    secrets = Secrets(**{k: SecretStr(v) for k, v in keys.items()})
    return cli.project_settings().model_copy(update={"secrets": secrets})


def test_names_each_missing_key() -> None:
    settings = _settings(anthropic_api_key="x")
    assert cli.missing_keys(settings) == ["OPENROUTER_API_KEY"]


def test_blank_keys_count_as_missing() -> None:
    settings = _settings(anthropic_api_key="", openrouter_api_key="")
    assert cli.missing_keys(settings) == ["ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"]


def test_make_client_stops_with_a_message(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit):
        cli.make_client(_settings())
    assert "OPENROUTER_API_KEY" in capsys.readouterr().out


def test_read_only_commands_need_no_keys(tmp_path, monkeypatch) -> None:
    """`status`, `show` and `costs` make no LLM calls, so they must not ask for keys."""
    settings = _settings()
    monkeypatch.setattr(cli, "project_settings", lambda: settings)
    monkeypatch.setattr(cli, "make_client", lambda s: pytest.fail("no client needed"))
    with pytest.raises(typer.Exit):  # the run doesn't exist; it exits at that check instead
        cli._deps("missing-run", tmp_path, read_only=True)
