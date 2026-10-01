"""run_tool_loop with FakeLLM: dispatch, output caps, stop conditions, ledger rows."""

import pytest
from pydantic import BaseModel
from samples import milestone_spec

from r2r.llm.fake import FakeLLM, tool_call
from r2r.llm.gateway import CallContext, run_tool_loop
from r2r.llm.tools import Tool, ToolLoopState


class AddArgs(BaseModel):
    a: int
    b: int


class NoArgs(BaseModel):
    pass


def _boom(_: NoArgs) -> str:
    raise RuntimeError("tool crashed")


TOOLS = [
    Tool(
        name="add",
        description="Add two integers.",
        input_model=AddArgs,
        handler=lambda x: str(x.a + x.b),
    ),
    Tool(
        name="big",
        description="Return a lot of text.",
        input_model=NoArgs,
        handler=lambda _: "x" * 10_000,
        max_output_chars=100,
    ),
    Tool(name="boom", description="Always fails.", input_model=NoArgs, handler=_boom),
]


def _loop(ctx: CallContext, max_turns: int = 5, stop_fn=lambda s: False):
    return run_tool_loop("coder", "coder", milestone_spec(), TOOLS, max_turns, stop_fn, ctx)


def _tool_messages(fake: FakeLLM, call: int) -> list[dict]:
    return [m for m in fake.calls[call].messages if m["role"] == "tool"]


def test_stops_when_model_stops_calling_tools(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("coder", [tool_call("add", a=2, b=3)], "done")
    res = _loop(ctx)
    assert res.stopped_by == "no_tool_calls"
    assert res.final_text == "done"
    assert res.turns == 2
    assert _tool_messages(fake, 1)[0]["content"] == "5"
    assert len(ctx.ledger.rows("run-test")) == 2  # one row per turn


def test_stop_fn(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("coder", [tool_call("add", a=1, b=1)], [tool_call("add", a=2, b=2)])

    def stop(state: ToolLoopState) -> bool:
        return state.last_results[-1].output == "4"

    res = _loop(ctx, stop_fn=stop)
    assert res.stopped_by == "stop_fn"
    assert res.turns == 2


def test_max_turns(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("coder", *[[tool_call("add", a=1, b=1)]] * 3)
    res = _loop(ctx, max_turns=3)
    assert res.stopped_by == "max_turns"
    assert res.turns == 3
    assert len(fake.calls) == 3


def test_tool_output_capped(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("coder", [tool_call("big")], "ok")
    _loop(ctx)
    out = _tool_messages(fake, 1)[0]["content"]
    assert out.startswith("x" * 100)
    assert "truncated" in out
    assert len(out) < 200


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (tool_call("nope"), "unknown tool"),
        (tool_call("add", a="two", b=1), "invalid arguments"),
        (tool_call("boom"), "tool crashed"),
    ],
)
def test_tool_errors_reported_to_model(ctx: CallContext, fake: FakeLLM, call, expected) -> None:
    fake.script("coder", [call], "giving up")
    res = _loop(ctx)
    assert res.stopped_by == "no_tool_calls"
    assert expected in _tool_messages(fake, 1)[0]["content"]


def test_tools_sent_in_request(ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("coder", "nothing to do")
    _loop(ctx)
    req = fake.calls[0]
    assert [t["function"]["name"] for t in req.tools] == ["add", "big", "boom"]
    assert req.tool_choice == "auto"
    assert "<data>" in req.messages[1]["content"]
