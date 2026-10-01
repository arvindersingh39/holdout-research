"""FakeLLM: deterministic, scripted per role, zero tokens; records calls (Phase 1)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict, deque
from itertools import count
from typing import Any

from pydantic import BaseModel

from r2r.contracts import Contract
from r2r.llm.client import EmbedResponse, LLMRequest, LLMResponse, ToolCall
from r2r.llm.tokens import estimate_tokens

_META = {"created_by", "run_id", "milestone_id"}
EMBED_DIM = 64
_ids = count(1)

# A scripted reply is one of:
#   Contract/BaseModel -> its JSON (or a call to the forced submit tool), without run metadata
#   str                -> raw text, e.g. invalid JSON to exercise the repair path
#   list[ToolCall]     -> tool calls (use `tool_call()`)
#   LLMResponse        -> returned as-is
#   Exception          -> raised
Scripted = BaseModel | str | list[ToolCall] | LLMResponse | Exception


def tool_call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"call_{next(_ids)}", name=name, arguments=json.dumps(arguments))


def contract_json(obj: BaseModel) -> str:
    exclude = _META if isinstance(obj, Contract) else None
    return obj.model_dump_json(exclude=exclude)


class FakeLLM:
    def __init__(
        self, prices: tuple[float, float] = (1e-6, 5e-6), native_schema: bool = True
    ) -> None:
        self._queues: dict[str, deque[Scripted]] = defaultdict(deque)
        self._prices = prices
        self._native = native_schema
        self.calls: list[LLMRequest] = []
        self.embed_calls: list[tuple[str, int]] = []

    def script(self, role: str, *replies: Scripted) -> None:
        self._queues[role].extend(replies)

    def pending(self, role: str) -> int:
        return len(self._queues[role])

    def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls.append(req)
        if not self._queues[req.role]:
            raise AssertionError(f"FakeLLM: no scripted reply left for role {req.role!r}")
        item = self._queues[req.role].popleft()
        if isinstance(item, Exception):
            raise item
        if isinstance(item, LLMResponse):
            return item
        text: str | None = None
        calls: list[ToolCall] = []
        if isinstance(item, BaseModel):
            if req.tools and isinstance(req.tool_choice, dict):  # forced submit tool
                name = req.tool_choice["function"]["name"]
                calls = [
                    ToolCall(id=f"call_{next(_ids)}", name=name, arguments=contract_json(item))
                ]
            else:
                text = contract_json(item)
        elif isinstance(item, list):
            calls = item
        else:
            text = item
        out_text = (text or "") + "".join(c.arguments for c in calls)
        return LLMResponse(
            model=req.model,
            text=text,
            tool_calls=calls,
            input_tokens=estimate_tokens(json.dumps(req.messages, default=str)),
            output_tokens=max(1, estimate_tokens(out_text)),
        )

    def embed(self, model: str, texts: list[str]) -> EmbedResponse:
        """Bag-of-words hashing vectors: deterministic, and similar texts score as similar."""
        self.embed_calls.append((model, len(texts)))
        return EmbedResponse(
            vectors=[_hash_vector(t) for t in texts],
            input_tokens=sum(estimate_tokens(t) for t in texts),
        )

    def supports_response_schema(self, model: str) -> bool:
        return self._native

    def prices(self, model: str) -> tuple[float, float] | None:
        return (0.0, 0.0) if model.startswith("local/") else self._prices


def _hash_vector(text: str) -> list[float]:
    vec = [0.0] * EMBED_DIM
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        vec[int(hashlib.sha256(word.encode()).hexdigest(), 16) % EMBED_DIM] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec] if any(vec) else [1.0 / math.sqrt(EMBED_DIM)] * EMBED_DIM
