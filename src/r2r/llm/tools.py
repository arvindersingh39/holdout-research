"""Tool schemas, dispatch and per-tool output caps for tool loops."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from r2r.llm.client import ToolCall


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[Any], str]
    max_output_chars: int = 4000

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    output: str
    ok: bool


@dataclass
class ToolLoopState:
    turns: int = 0
    last_results: list[ToolResult] = field(default_factory=list)


@dataclass(frozen=True)
class ToolLoopResult:
    turns: int
    stopped_by: Literal["stop_fn", "max_turns", "no_tool_calls"]
    final_text: str | None
    state: ToolLoopState


def cap_output(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[... truncated: {len(text) - max_chars} more chars]"


def dispatch(call: ToolCall, tools: dict[str, Tool]) -> ToolResult:
    """Run one tool call. Failures come back as text so the model can correct itself."""
    tool = tools.get(call.name)
    if tool is None:
        return ToolResult(call.id, call.name, f"error: unknown tool {call.name!r}", ok=False)
    try:
        args = tool.input_model.model_validate(json.loads(call.arguments or "{}"))
    except (ValueError, ValidationError) as e:
        return ToolResult(call.id, call.name, f"error: invalid arguments: {e}"[:1000], ok=False)
    try:
        out = tool.handler(args)
    except Exception as e:
        return ToolResult(call.id, call.name, f"error: {type(e).__name__}: {e}"[:1000], ok=False)
    return ToolResult(call.id, call.name, cap_output(str(out), tool.max_output_chars), ok=True)
