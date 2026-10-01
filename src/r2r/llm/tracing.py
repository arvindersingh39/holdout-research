"""Langfuse tracing through the Langfuse SDK directly (D47, revised).

LiteLLM's `langfuse_otel` callback needs LiteLLM's proxy extras, so the client records each call
itself: one generation per provider call, tagged with run, milestone and role. Tracing must never
break a call, and only the host process traces; sandboxes never see the keys.
"""

from __future__ import annotations

import atexit
import contextlib
from typing import TYPE_CHECKING, Any

from langfuse import propagate_attributes

from r2r.log import get_logger

if TYPE_CHECKING:
    from r2r.llm.client import LLMRequest, LLMResponse
    from r2r.settings import Secrets

log = get_logger("r2r.llm.tracing")


class Tracer:
    def __init__(self, client: Any) -> None:
        self._lf = client

    @classmethod
    def from_secrets(cls, secrets: Secrets) -> Tracer | None:
        pk, sk, host = (
            secrets.langfuse_public_key,
            secrets.langfuse_secret_key,
            secrets.langfuse_host,
        )
        if not (pk and sk and host):
            return None
        from langfuse import Langfuse

        lf = Langfuse(
            public_key=pk.get_secret_value(), secret_key=sk.get_secret_value(), base_url=host
        )
        tracer = cls(lf)
        atexit.register(tracer.flush)
        return tracer

    def record(self, req: LLMRequest, resp: LLMResponse | None, error: str | None = None) -> None:
        md = req.metadata
        try:
            with propagate_attributes(
                session_id=md.get("session_id"),
                trace_name=md.get("trace_name"),
                tags=md.get("tags"),
            ):
                obs = self._lf.start_observation(
                    name=req.role,
                    as_type="generation",
                    model=req.model,
                    input=req.messages,
                    output=(resp.text or [c.model_dump() for c in resp.tool_calls])
                    if resp
                    else None,
                    usage_details={
                        "input": resp.input_tokens,
                        "output": resp.output_tokens,
                        "cache_read": resp.cache_read_tokens,
                    }
                    if resp
                    else None,
                    cost_details={"total": resp.cost_usd} if resp and resp.cost_usd else None,
                    level="ERROR" if error else None,
                    status_message=error,
                )
                obs.end()
        except Exception as e:  # tracing must never break a call
            log.warning("tracing failed", extra={"error": f"{type(e).__name__}"})

    def flush(self) -> None:
        with contextlib.suppress(Exception):  # best effort at exit
            self._lf.flush()
