"""#1 Retriever: proposal and papers to PaperCards and a ResearchBrief (Phase 2).

Papers reach the model one at a time, inside <document> tags, only for their PaperCard.
The brief is built from the proposal plus PaperCards, never from raw papers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model

from r2r.contracts import BlobRef, PaperCard, Proposal, ResearchBrief, ensure_within_cap
from r2r.ingest.citations import to_bibtex
from r2r.ingest.documents import wrap_document
from r2r.llm.gateway import CallContext, call_structured
from r2r.store.blobs import BlobStore
from r2r.store.runs import RunPaths

ROLE = "retriever"
_FILLED_BY_CODE = {"created_by", "run_id", "milestone_id", "paper_cards"}

# What the model returns for the brief: ResearchBrief minus the fields code fills in (D20).
BriefDraft: type[BaseModel] = create_model(  # type: ignore[call-overload]
    "ResearchBriefDraft",
    __config__=ConfigDict(extra="forbid"),
    **{
        n: (f.annotation, f)
        for n, f in ResearchBrief.model_fields.items()
        if n not in _FILLED_BY_CODE
    },
)


class PaperInput(BaseModel):
    paper: str  # wrapped in <document> tags


class BriefInput(BaseModel):
    proposal: str  # wrapped in <document> tags
    paper_cards: list[PaperCard]


@dataclass(frozen=True)
class RetrieverResult:
    brief: ResearchBrief
    brief_ref: BlobRef
    card_refs: list[BlobRef]
    bib_path: Path


def run_retriever(
    proposal: Proposal, blobs: BlobStore, paths: RunPaths, ctx: CallContext
) -> RetrieverResult:
    limit = ctx.settings.budgets.ingest.paper_card_input_tokens
    model = ctx.settings.models.roles[ROLE].model

    cards: list[PaperCard] = []
    card_refs: list[BlobRef] = []
    for ref in proposal.paper_refs:
        doc = wrap_document(blobs.get_slice(ref, limit), source=ref.summary[:80])
        card = call_structured(ROLE, "retriever", PaperInput(paper=doc), PaperCard, ctx)
        cards.append(card)
        card_refs.append(
            blobs.put(
                card.model_dump_json(),
                kind="contract",
                summary=f"PaperCard: {card.citation.title}"[:300],
                created_by=ROLE,
            )
        )

    proposal_doc = wrap_document(blobs.get_slice(proposal.text_ref, limit), source="proposal.md")
    draft = call_structured(
        ROLE,
        "retriever_brief",
        BriefInput(proposal=proposal_doc, paper_cards=cards),
        BriefDraft,
        ctx,
    )
    data: dict[str, Any] = {
        **draft.model_dump(),
        "created_by": ROLE,
        "run_id": ctx.run_id,
        "milestone_id": None,
        "paper_cards": [r.model_dump() for r in card_refs],
    }
    brief = ensure_within_cap(ResearchBrief.model_validate(data), model)
    brief_ref = blobs.put(
        brief.model_dump_json(),
        kind="contract",
        summary=f"ResearchBrief: {proposal.title}"[:300],
        created_by=ROLE,
    )

    bib = to_bibtex(cards)
    bib_path = paths.root / "refs.bib"
    bib_path.write_text(bib)
    if bib:
        blobs.put(bib, kind="bib", summary=f"refs.bib ({len(cards)} entries)", created_by=ROLE)
    return RetrieverResult(brief=brief, brief_ref=brief_ref, card_refs=card_refs, bib_path=bib_path)
