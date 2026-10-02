"""Scout: finds published baselines for a milestone's tests; verifies every quote (D52/D53).

1. The model proposes search queries.  2. Code searches (Tavily) and extracts full page text.
3. The model reads the pages (as <document> data) and proposes BaselineCards with exact quotes.
4. Code keeps only cards whose quote appears in the fetched page and states the value.
Failures (no key, disabled, search errors) never stop the run; the designer is told why.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, Field

from r2r.contracts import BaselineCard, Criterion, Milestone, MilestoneSpec
from r2r.contracts.base import Text
from r2r.ingest.documents import wrap_document
from r2r.llm.gateway import CallContext, call_structured
from r2r.log import get_logger
from r2r.search.tavily import CREDIT_USD, SearchError, TavilyClient
from r2r.store.blobs import BlobStore
from r2r.store.ledger import CallRecord

log = get_logger("r2r.agents.scout")
ROLE = "scout"
MINUS = "\u2212"  # typographic minus, common in published tables
_NUM = re.compile(r"-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")


class QueryInput(BaseModel):
    goal: str
    milestone: Milestone
    acceptance_criteria: list[Criterion]


class QueryPlan(BaseModel):
    queries: Annotated[list[Text(200, 3)], Field(min_length=1, max_length=10)]


class PageDoc(BaseModel):
    url: str
    title: str
    text: str  # wrapped in <document> tags


class ReadInput(BaseModel):
    acceptance_criteria: list[Criterion]
    pages: list[PageDoc]


class CardDraft(BaseModel):
    criterion_id: str
    claim: Text(300, 1)
    value: float | None = None
    units: Text(60) = ""
    precision: Text(100) = ""
    source_url: str
    source_title: Text(200) = ""
    quote: Text(400, 1)
    license_note: Text(200) = ""


class CardList(BaseModel):
    cards: Annotated[list[CardDraft], Field(max_length=12)] = []


@dataclass(frozen=True)
class ScoutResult:
    cards: list[BaselineCard]
    note: str


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace(MINUS, "-")).strip().lower()


def verify_card(card: BaselineCard, pages: dict[str, str]) -> bool:
    """The quote must appear in the fetched page, and a numeric value must be stated in it."""
    page = pages.get(card.source_url)
    if page is None or _norm(card.quote) not in _norm(page):
        return False
    if card.value is None:
        return True
    for raw in _NUM.findall(card.quote.replace(MINUS, "-")):
        try:
            n = float(raw.replace(",", "."))
        except ValueError:
            continue
        if math.isclose(n, card.value, rel_tol=1e-9, abs_tol=1e-15):
            return True
    return False


def _record(ctx: CallContext, kind: str, credits: int) -> None:
    ctx.ledger.record(
        CallRecord(
            run_id=ctx.run_id,
            milestone_id=ctx.milestone_id,
            role="search",
            model=f"tavily/{kind}",
            cost_usd=credits * CREDIT_USD,
        )
    )


def run_scout(
    spec: MilestoneSpec,
    milestone: Milestone,
    goal: str,
    client: TavilyClient | None,
    blobs: BlobStore,
    ctx: CallContext,
) -> ScoutResult:
    cfg = ctx.settings.budgets.baselines
    if not cfg.enabled:
        return ScoutResult([], "Baseline search disabled (baselines.enabled: false).")
    if client is None:
        return ScoutResult([], "No TAVILY_API_KEY set: baseline search skipped.")
    plan = call_structured(
        ROLE,
        "scout_queries",
        QueryInput(goal=goal, milestone=milestone, acceptance_criteria=spec.acceptance_criteria),
        QueryPlan,
        ctx,
    )
    titles: dict[str, str] = {}
    try:
        for q in plan.queries[: cfg.max_queries]:
            hits, credits = client.search(q)
            _record(ctx, "search", credits)
            for h in hits:
                if len(titles) < cfg.max_pages and h.url not in titles:
                    titles[h.url] = h.title
        pages, credits = client.extract(list(titles))
        _record(ctx, "extract", credits)
    except SearchError as e:
        log.warning("baseline search failed", extra={"error": str(e)[:200]})
        return ScoutResult([], f"Baseline search failed ({str(e)[:150]}); no baselines.")
    texts = {p.url: p.text for p in pages if p.text.strip()}
    if not texts:
        return ScoutResult([], "Search found no readable pages; no baselines.")
    for url, text in texts.items():
        blobs.put(text, kind="other", summary=f"baseline source: {url}"[:300], created_by=ROLE)
    limit = cfg.page_tokens * 4
    docs = [
        PageDoc(url=u, title=titles.get(u, ""), text=wrap_document(t[:limit], source=u))
        for u, t in texts.items()
    ]
    found = call_structured(
        ROLE,
        "scout_read",
        ReadInput(acceptance_criteria=spec.acceptance_criteria, pages=docs),
        CardList,
        ctx,
    )
    criteria = {c.id for c in spec.acceptance_criteria}
    kept: list[BaselineCard] = []
    for d in found.cards:
        if d.criterion_id not in criteria:
            continue
        try:
            card = BaselineCard(
                created_by=ROLE,
                run_id=ctx.run_id,
                milestone_id=ctx.milestone_id,
                verified=False,
                **d.model_dump(),
            )
        except ValueError:
            continue
        if verify_card(card, texts):
            kept.append(card.model_copy(update={"verified": True}))
    dropped = len(found.cards) - len(kept)
    note = f"{len(kept)} verified baseline(s) from {len(texts)} page(s); {dropped} dropped."
    return ScoutResult(kept, note)
