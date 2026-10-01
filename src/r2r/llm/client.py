"""The LLM client interface, and the LiteLLM implementation behind it.

This module and `tokens.py` are the only places that import `litellm`. The gateway talks
to an `LLMClient`, so tests can swap in `FakeLLM`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from r2r.settings import Secrets


class LLMCallError(Exception):
    """The provider rejected the call (bad request, auth, no credit, ...). Not retried."""


class TransientLLMError(LLMCallError):
    """Rate limit, 5xx, timeout or connection error. Retried with backoff."""


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str  # JSON text, as the provider returned it


class LLMResponse(BaseModel):
    model: str
    text: str | None = None
    tool_calls: list[ToolCall] = []
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None  # None: the gateway prices it
    latency_ms: int = 0
    reasoning_tokens: int = 0  # thinking tokens billed (Anthropic returns no thinking text)


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    input_tokens: int = 0
    cost_usd: float | None = None  # None: the gateway prices it


@dataclass
class LLMRequest:
    role: str
    model: str
    messages: list[dict[str, Any]]
    max_tokens: int
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    thinking: dict[str, Any] | None = None
    timeout_s: float = 300.0
    extra: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)  # trace tags (Langfuse, D47)


class LLMClient(Protocol):
    def complete(self, req: LLMRequest) -> LLMResponse: ...

    def _trace(self, req: LLMRequest, resp: LLMResponse | None, error: str | None = None) -> None:
        if self._tracer is not None:
            self._tracer.record(req, resp, error)

    def embed(self, model: str, texts: list[str]) -> EmbedResponse: ...

    def supports_response_schema(self, model: str) -> bool: ...

    def prices(self, model: str) -> tuple[float, float] | None:
        """USD per token (input, output), or None if unknown."""
        ...


_KEY_FIELD = {
    "anthropic": "anthropic_api_key",
    "gemini": "gemini_api_key",
    "openrouter": "openrouter_api_key",
}


LOCAL_EMBEDDERS = {"local/all-MiniLM-L6-v2"}


class LiteLLMClient:
    def __init__(self, secrets: Secrets) -> None:
        self._secrets = secrets
        self._local_embedder: Any = None
        from r2r.llm.tracing import Tracer

        self._tracer = Tracer.from_secrets(secrets)
        self.tracing = self._tracer is not None

    def _api_key(self, model: str) -> str | None:
        field_name = _KEY_FIELD.get(model.split("/", 1)[0])
        key = getattr(self._secrets, field_name) if field_name else None
        return key.get_secret_value() if key else None

    def complete(self, req: LLMRequest) -> LLMResponse:
        import litellm

        kwargs: dict[str, Any] = {
            "model": req.model,
            "messages": req.messages,
            "max_tokens": req.max_tokens,
            "timeout": req.timeout_s,
            "api_key": self._api_key(req.model),
            **req.extra,
        }
        for name in ("response_format", "tools", "tool_choice", "thinking"):
            if (value := getattr(req, name)) is not None:
                kwargs[name] = value
        transient = (
            litellm.RateLimitError,
            litellm.InternalServerError,
            litellm.ServiceUnavailableError,
            litellm.APIConnectionError,
            litellm.Timeout,
        )
        start = time.monotonic()
        try:
            resp = litellm.completion(**kwargs)
        except transient as e:
            self._trace(req, None, _short(e))
            raise TransientLLMError(_short(e)) from None
        except Exception as e:
            self._trace(req, None, _short(e))
            raise LLMCallError(_short(e)) from None
        latency = int((time.monotonic() - start) * 1000)

        msg = resp.choices[0].message
        usage = resp.usage
        details = getattr(usage, "prompt_tokens_details", None)
        cache_read = getattr(usage, "cache_read_input_tokens", None) or (
            getattr(details, "cached_tokens", None) if details else None
        )
        try:
            cost = float(litellm.completion_cost(completion_response=resp))
        except Exception:
            cost = None
        out = LLMResponse(
            model=req.model,
            text=msg.content,
            tool_calls=[
                ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
                for tc in (msg.tool_calls or [])
            ],
            input_tokens=usage.prompt_tokens or 0,
            output_tokens=usage.completion_tokens or 0,
            cache_read_tokens=cache_read or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
            cost_usd=cost,
            latency_ms=latency,
            reasoning_tokens=getattr(
                getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None
            )
            or 0,
        )
        self._trace(req, out)
        return out

    def embed(self, model: str, texts: list[str]) -> EmbedResponse:
        if model.startswith("local/"):
            return EmbedResponse(vectors=self._embed_local(model, texts))
        import litellm

        try:
            resp = litellm.embedding(model=model, input=texts, api_key=self._api_key(model))
        except Exception as e:
            raise LLMCallError(_short(e)) from None
        usage = getattr(resp, "usage", None)
        try:
            cost = float(litellm.completion_cost(completion_response=resp))
        except Exception:
            cost = None
        return EmbedResponse(
            vectors=[list(map(float, d["embedding"])) for d in resp.data],
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            cost_usd=cost,
        )

    def _embed_local(self, model: str, texts: list[str]) -> list[list[float]]:
        """Chroma's bundled MiniLM ONNX model: runs on this machine, downloaded once (~80 MB)."""
        if model not in LOCAL_EMBEDDERS:
            raise LLMCallError(f"unknown local embedding model {model}")
        if self._local_embedder is None:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            self._local_embedder = DefaultEmbeddingFunction()
        return [list(map(float, v)) for v in self._local_embedder(texts)]

    def supports_response_schema(self, model: str) -> bool:
        import litellm

        try:
            return bool(litellm.supports_response_schema(model))
        except Exception:
            return False

    def prices(self, model: str) -> tuple[float, float] | None:
        if model.startswith("local/"):
            return 0.0, 0.0
        import litellm

        try:
            info = litellm.get_model_info(model)
        except Exception:
            return None
        inp, out = info.get("input_cost_per_token"), info.get("output_cost_per_token")
        if not inp or not out:  # a 0 price is LiteLLM's "unknown", not "free"
            return None
        return float(inp), float(out)


def _short(e: Exception) -> str:
    """First line of a provider error, typed. Provider messages never contain the API key."""
    first = str(e).splitlines()[0] if str(e) else ""
    return f"{type(e).__name__}: {first[:300]}"
