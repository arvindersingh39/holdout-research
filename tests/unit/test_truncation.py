"""pilot-2 M5: a reply cut off at max_tokens arrived as a tool call without `content`, and the
coder repeated it for two whole sessions. Cut-off replies are now detected, identical failing calls
end the session, and big files can be written in parts."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from r2r.agents.coder import AppendArgs, CoderTools, CreateArgs
from r2r.harness.sandbox import LocalProcessBackend
from r2r.harness.workspace import UnsafePath, Workspace
from r2r.llm.client import LLMResponse
from r2r.llm.fake import FakeLLM, tool_call
from r2r.llm.gateway import CallContext, run_tool_loop
from r2r.llm.tools import Tool
from r2r.store.runs import run_paths


class _P(BaseModel):
    x: int = 1


class _Note(BaseModel):
    text: str


def _tools(log: list) -> list[Tool]:
    return [Tool("note", "Write a note.", _Note, lambda a: log.append(a.text) or "ok", 100)]


def _cut(name: str = "note", **args) -> LLMResponse:
    return LLMResponse(
        model="m", tool_calls=[tool_call(name, **args)], output_tokens=4000, truncated=True
    )


def test_truncated_tool_call_is_not_run_and_the_model_is_told(
    ctx: CallContext, fake: FakeLLM
) -> None:
    log: list = []
    fake.script("coder", _cut(), [tool_call("note", text="short")], "done")
    res = run_tool_loop("coder", "coder", _P(), _tools(log), 10, lambda s: False, ctx)
    assert log == ["short"]  # the cut-off call never ran
    second = fake.calls[1].messages
    tool_msg = next(m for m in reversed(second) if m["role"] == "tool")
    text = json.dumps(tool_msg["content"])
    assert "cut off" in text and "append_file" in text
    assert res.stopped_by == "no_tool_calls"


def test_identical_failures_end_the_session(ctx: CallContext, fake: FakeLLM) -> None:
    bad = [tool_call("note")]  # missing `text`, every time
    fake.script("coder", bad, bad, bad, bad, bad)
    res = run_tool_loop("coder", "coder", _P(), _tools([]), 25, lambda s: False, ctx)
    assert res.stopped_by == "stuck" and res.turns == 3
    assert "same failing call" in (res.final_text or "")


def test_varied_failures_do_not_trip_the_guard(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script(
        "coder",
        [tool_call("note")],
        [tool_call("nope")],
        [tool_call("note")],
        [tool_call("note", text="ok")],
        "done",
    )
    res = run_tool_loop("coder", "coder", _P(), _tools([]), 25, lambda s: False, ctx)
    assert res.stopped_by == "no_tool_calls"


@pytest.fixture
def tools(tmp_path: Path) -> CoderTools:
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    return CoderTools(ws, LocalProcessBackend(ws), "run-test", "M1", 60)


def test_append_file_writes_big_files_in_parts(tools: CoderTools) -> None:
    tools.create_file(CreateArgs(path="src/pkg/big.py", content="a = 1\n"))
    tools.append_file(AppendArgs(path="src/pkg/big.py", content="b = 2\n"))
    tools.append_file(AppendArgs(path="src/pkg/new.py", content="c = 3\n"))  # creates
    root = tools.ws.root
    assert (root / "src/pkg/big.py").read_text() == "a = 1\nb = 2\n"
    assert (root / "src/pkg/new.py").read_text() == "c = 3\n"
    with pytest.raises(UnsafePath):  # only code folders are writable
        tools.append_file(AppendArgs(path="tests/test_x.py", content="x"))
    assert "append_file" in {t.name for t in tools.tools()}


def test_client_marks_length_finish_as_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    import litellm

    from r2r.llm.client import LiteLLMClient, LLMRequest
    from r2r.settings import Secrets

    client = LiteLLMClient(Secrets())
    client._tracer = type("T", (), {"record": lambda self, *a: None})()
    req = LLMRequest(role="coder", model="anthropic/claude-sonnet-5-5", messages=[], max_tokens=5)
    for reason, cut in (("length", True), ("max_tokens", True), ("stop", False)):
        resp = litellm.ModelResponse(
            model="x",
            choices=[{"message": {"role": "assistant", "content": "hi"}, "finish_reason": reason}],
            usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        )
        monkeypatch.setattr(litellm, "completion", lambda r=resp, **kw: r)
        assert client.complete(req).truncated is cut, reason
