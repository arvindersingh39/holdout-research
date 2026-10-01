"""Live gateway smoke tests: real API calls, run only with R2R_LIVE=1 after owner approval.

Estimated total cost: under $0.03 (cap $0.05).
"""

from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from r2r.llm.client import LiteLLMClient, LLMRequest, LLMResponse
from r2r.llm.gateway import CallContext, call_structured
from r2r.settings import load_settings
from r2r.store.ledger import Ledger

REPO = Path(__file__).resolve().parents[2]
PROMPTS = Path(__file__).parent / "prompts"


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    answer: int
    reason: str = Field(max_length=200)


class RecordingClient(LiteLLMClient):
    """Keeps every raw response so the test can compare it with the ledger."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.responses: list[LLMResponse] = []

    def complete(self, req: LLMRequest) -> LLMResponse:
        resp = super().complete(req)
        self.responses.append(resp)
        return resp


def _ctx(tmp_path: Path, prompts: Path = PROMPTS) -> tuple[CallContext, RecordingClient]:
    settings = load_settings(REPO / "config", REPO / ".env")
    client = RecordingClient(settings.secrets)
    ctx = CallContext(
        run_id="live",
        milestone_id=None,
        settings=settings,
        ledger=Ledger(tmp_path / "ledger.sqlite"),
        client=client,
        prompts_dir=prompts,
    )
    return ctx, client


@pytest.mark.parametrize(
    "role",
    ["short_planner", "retriever"],  # Claude Sonnet 5.5 direct; Gemini Flash via OpenRouter
)
def test_structured_call_and_ledger(tmp_path: Path, role: str) -> None:
    ctx, client = _ctx(tmp_path)
    out = call_structured(role, "echo", Question(text="What is 2 + 3?"), Answer, ctx)
    assert out.answer == 5

    rows = ctx.ledger.rows("live")
    assert len(rows) == len(client.responses) >= 1
    for row, resp in zip(rows, client.responses, strict=True):
        assert row.input_tokens == resp.input_tokens > 0
        assert row.output_tokens == resp.output_tokens > 0
        assert row.cost_usd > 0
        if resp.cost_usd is not None:
            assert row.cost_usd == pytest.approx(resp.cost_usd)
    print(
        f"\n{role}: {rows[-1].model} in={rows[-1].input_tokens} out={rows[-1].output_tokens} "
        f"cost=${sum(r.cost_usd for r in rows):.6f} calls={len(rows)}"
    )


def test_anthropic_prompt_caching(tmp_path: Path) -> None:
    """Two identical calls with a >1024-token system prefix: the second should read the cache."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    filler = "\n".join(f"Reference rule {i}: answers are integers." for i in range(400))
    (prompts / "echo.md").write_text((PROMPTS / "echo.md").read_text() + "\n" + filler)
    ctx, _ = _ctx(tmp_path, prompts)
    for _ in range(2):
        call_structured("short_planner", "echo", Question(text="What is 4 + 4?"), Answer, ctx)
    first, second = ctx.ledger.rows("live")[:2]
    print(
        f"\ncache: first write={first.cache_write_tokens} read={first.cache_read_tokens}; "
        f"second read={second.cache_read_tokens}"
    )
    assert first.cache_write_tokens > 0 or first.cache_read_tokens > 0
    assert second.cache_read_tokens > 0


def test_thinking_with_structured_output(tmp_path: Path) -> None:
    """long_planner uses Opus with a thinking budget; it must still return a valid contract."""
    ctx, _ = _ctx(tmp_path)
    out = call_structured("long_planner", "echo", Question(text="What is 6 * 7?"), Answer, ctx)
    assert out.answer == 42
    row = ctx.ledger.rows("live")[-1]
    print(f"\nthinking: out={row.output_tokens} cost=${row.cost_usd:.6f}")
