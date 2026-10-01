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


def test_tracing_enabled_only_with_all_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    import litellm

    monkeypatch.setattr(litellm, "callbacks", [])
    assert not LiteLLMClient(Secrets(langfuse_public_key=SecretStr("pk-lf-x"))).tracing
    full = Secrets(
        langfuse_public_key=SecretStr("pk-lf-x"),
        langfuse_secret_key=SecretStr("sk-lf-y"),
        langfuse_host="https://cloud.langfuse.com",
    )
    assert LiteLLMClient(full).tracing
    assert litellm.callbacks == []  # D47 revised: no LiteLLM callback, direct SDK instead


class FakeObservation:
    def __init__(self, log: list, **kw) -> None:
        self.kw, self.log = kw, log

    def end(self) -> None:
        self.log.append(("end", self.kw))


class FakeLangfuse:
    def __init__(self) -> None:
        self.log: list = []

    def start_observation(self, **kw):
        return FakeObservation(self.log, **kw)

    def flush(self) -> None:
        self.log.append(("flush", {}))


def test_tracer_records_generation_with_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    from r2r.llm import tracing
    from r2r.llm.client import LLMRequest, LLMResponse

    seen_attrs: list[dict] = []

    class Attrs:
        def __init__(self, **kw) -> None:
            seen_attrs.append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None

    monkeypatch.setattr(tracing, "propagate_attributes", Attrs)
    lf = FakeLangfuse()
    tr = tracing.Tracer(client=lf)
    req = LLMRequest(
        role="coder",
        model="anthropic/claude-sonnet-5-5",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=10,
        metadata={
            "session_id": "r1",
            "trace_name": "r1/M1/coder",
            "tags": ["run:r1", "milestone:M1", "role:coder"],
        },
    )
    tr.record(
        req,
        LLMResponse(
            model=req.model,
            text="ok",
            input_tokens=5,
            output_tokens=2,
            cache_read_tokens=1,
            cost_usd=0.001,
        ),
    )
    tr.record(req, None, error="RateLimitError: slow down")
    (_, kw1), (_, kw2) = lf.log
    assert kw1["as_type"] == "generation" and kw1["name"] == "coder"
    assert kw1["usage_details"] == {"input": 5, "output": 2, "cache_read": 1}
    assert kw1["cost_details"] == {"total": 0.001} and kw1["output"] == "ok"
    assert kw2["level"] == "ERROR" and "RateLimitError" in kw2["status_message"]
    assert seen_attrs[0] == {
        "session_id": "r1",
        "trace_name": "r1/M1/coder",
        "tags": ["run:r1", "milestone:M1", "role:coder"],
    }


def test_tracer_never_breaks_a_call() -> None:
    from r2r.llm import tracing
    from r2r.llm.client import LLMRequest, LLMResponse

    class Broken:
        def start_observation(self, **kw):
            raise RuntimeError("langfuse down")

    req = LLMRequest(role="coder", model="x/y", messages=[], max_tokens=1)
    tracing.Tracer(client=Broken()).record(req, LLMResponse(model="x/y"))  # no exception


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


def test_litellm_client_traces_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real client path (not FakeLLM): every provider call is handed to the tracer."""
    import litellm

    from r2r.llm.client import LLMCallError, LLMRequest

    recorded: list = []
    client = LiteLLMClient(Secrets())
    client._tracer = type("T", (), {"record": lambda self, *a: recorded.append(a)})()
    resp = litellm.ModelResponse(
        model="x",
        choices=[{"message": {"role": "assistant", "content": "hi"}}],
        usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    )
    monkeypatch.setattr(litellm, "completion", lambda **kw: resp)
    req = LLMRequest(role="coder", model="anthropic/claude-sonnet-5-5", messages=[], max_tokens=5)
    client.complete(req)

    def boom(**kw):
        raise ValueError("bad request")

    monkeypatch.setattr(litellm, "completion", boom)
    with pytest.raises(LLMCallError):
        client.complete(req)
    assert (
        recorded[0][1].text == "hi" and recorded[1][1] is None and "bad request" in recorded[1][2]
    )
