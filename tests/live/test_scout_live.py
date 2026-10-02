"""Live baseline scout (Tavily + Gemini Flash). R2R_LIVE=1, after approval. ≈14 credits + ≈$0.02."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "unit"))

from samples import milestone_spec

from r2r.agents.scout import run_scout
from r2r.contracts import Criterion, Milestone, MilestoneSpec
from r2r.llm.client import LiteLLMClient
from r2r.llm.gateway import CallContext
from r2r.search.tavily import TavilyClient
from r2r.settings import load_settings
from r2r.store.blobs import BlobStore
from r2r.store.ledger import Ledger

REPO = Path(__file__).resolve().parents[2]
AC = [
    Criterion(
        id="AC1",
        text="Black-Scholes European call with S=100, K=100, r=0.05, sigma=0.2, "
        "T=1 year has price 10.4506 (put 5.5735) within 1e-4.",
    )
]


def test_scout_finds_a_verified_baseline(tmp_path: Path) -> None:
    settings = load_settings(REPO / "config", REPO / ".env")
    assert settings.secrets.tavily_api_key, "set TAVILY_API_KEY in .env"
    ctx = CallContext(
        run_id="live-scout",
        milestone_id="M1",
        settings=settings,
        ledger=Ledger(tmp_path / "l.sqlite"),
        client=LiteLLMClient(settings.secrets),
        prompts_dir=REPO / "prompts",
    )
    spec = MilestoneSpec.model_validate(
        {**milestone_spec().model_dump(), "acceptance_criteria": [c.model_dump() for c in AC]}
    )
    ms = Milestone(id="M1", title="Black-Scholes closed-form pricer", acceptance_criteria=AC)
    res = run_scout(
        spec,
        ms,
        "Price European options",
        TavilyClient(settings.secrets.tavily_api_key.get_secret_value()),
        BlobStore(tmp_path / "b"),
        ctx,
    )
    print(f"\n{res.note}")
    for c in res.cards:
        print(
            f'- {c.criterion_id}: {c.claim} = {c.value} {c.units}\n  "{c.quote}"\n  {c.source_url}'
        )
    for r in ctx.ledger.rows("live-scout"):
        print(f"  ledger {r.role:7} {r.model:40} ${r.cost_usd:.4f}")
    assert res.cards, "no verified baseline found"
