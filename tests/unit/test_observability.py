"""Langfuse tracing (D47) and `r2r costs`."""

from pathlib import Path

import pytest
from pydantic import SecretStr
from samples import roadmap
from typer.testing import CliRunner

from r2r.llm.client import LiteLLMClient
from r2r.llm.fake import FakeLLM
from r2r.llm.gateway import CallContext, call_structured
from r2r.settings import Secrets
from r2r.store.ledger import CallRecord, Ledger


def test_requests_carry_trace_metadata(ctx: CallContext, fake: FakeLLM) -> None:
    from samples import research_brief

    from r2r.contracts import Roadmap

    fake.script("long_planner", roadmap())
    call_structured("long_planner", "long_planner", research_brief(), Roadmap, ctx)
    md = fake.calls[0].metadata
    assert md["generation_name"] == "long_planner"
    assert md["session_id"] == "run-test"
    assert {"run:run-test", "milestone:M1", "role:long_planner"} <= set(md["tags"])


def test_langfuse_enabled_only_with_all_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    import litellm

    monkeypatch.setattr(litellm, "callbacks", [])
    for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_OTEL_HOST"):
        monkeypatch.delenv(k, raising=False)
    assert not LiteLLMClient(Secrets(langfuse_public_key=SecretStr("pk-lf-x"))).tracing
    assert litellm.callbacks == []
    full = Secrets(
        langfuse_public_key=SecretStr("pk-lf-x"),
        langfuse_secret_key=SecretStr("sk-lf-y"),
        langfuse_host="https://cloud.langfuse.com",
    )
    client = LiteLLMClient(full)
    assert client.tracing
    assert litellm.callbacks.count("langfuse_otel") == 1
    LiteLLMClient(full)  # a second client doesn't register the callback twice
    assert litellm.callbacks.count("langfuse_otel") == 1


def test_costs_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import r2r.cli as cli
    from r2r.graph.deps import RunDeps
    from r2r.settings import load_settings

    repo = Path(__file__).resolve().parents[2]
    deps = RunDeps.create(
        "c1",
        repo / "examples/pilot_mc_pricer",
        load_settings(repo / "config", env_file=None),
        FakeLLM(),
        tmp_path,
        repo / "prompts",
    )
    led: Ledger = deps.ledger
    rows = [
        ("long_planner", None, "anthropic/claude-opus-5-5", 3000, 2000, 0, 0.052),
        ("coder", "M1", "anthropic/claude-sonnet-5-5", 15000, 1200, 6000, 0.036),
        ("coder", "M1", "anthropic/claude-sonnet-5-5", 9000, 800, 8000, 0.012),
        ("optimizer", "M1", "openrouter/google/gemini-3.8-flash", 2000, 900, 0, 0.004),
    ]
    for role, ms, model, i, o, cr, cost in rows:
        led.record(
            CallRecord(
                run_id="c1",
                milestone_id=ms,
                role=role,
                model=model,
                input_tokens=i,
                output_tokens=o,
                cache_read_tokens=cr,
                cost_usd=cost,
            )
        )
    monkeypatch.setattr(cli, "make_client", lambda s: FakeLLM())
    out = CliRunner().invoke(
        cli.app, ["costs", "c1", "--runs-base", str(tmp_path)], env={"COLUMNS": "140"}
    )
    assert out.exit_code == 0, out.output
    text = out.output
    assert "coder" in text and "0.0480" in text  # 0.036 + 0.012
    assert "openrouter/google/gemini-3.8-flash" in text
    assert "M1" in text and "run-level" in text
    assert "Total" in text and "0.1040" in text
    assert "58%" in text  # coder cache-read share: 14000 / 24000
