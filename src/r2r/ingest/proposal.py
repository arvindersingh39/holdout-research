"""Proposal folder → blobs, chunks and vectors: `proposal.md` plus optional `papers/*.pdf`."""

from __future__ import annotations

from pathlib import Path

from r2r.contracts import Proposal
from r2r.ingest.chunk import chunk_text
from r2r.ingest.pdf import extract_pdf
from r2r.llm.gateway import CallContext
from r2r.log import get_logger
from r2r.retrieval.vectors import VectorIndex
from r2r.store.blobs import BlobStore

log = get_logger("r2r.ingest")

TITLE_CHARS = 200
SUMMARY_CHARS = 300


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()[:TITLE_CHARS] or fallback
    return fallback[:TITLE_CHARS]


def _index_chunks(
    text: str, source: str, blobs: BlobStore, index: VectorIndex, ctx: CallContext
) -> int:
    limits = ctx.settings.budgets.ingest
    chunks = chunk_text(text, limits.chunk_tokens, limits.chunk_overlap_tokens)
    refs = [
        blobs.put(
            c.text, kind="chunk", summary=f"{source}: {c.summary}"[:SUMMARY_CHARS], created_by="cli"
        )
        for c in chunks
    ]
    index.add(refs, [c.text for c in chunks])
    return len(chunks)


def ingest_proposal(
    proposal_dir: Path, blobs: BlobStore, index: VectorIndex, ctx: CallContext
) -> Proposal:
    proposal_dir = Path(proposal_dir)
    md = proposal_dir / "proposal.md"
    if not md.is_file():
        raise FileNotFoundError(f"{md} not found")
    text = md.read_text()
    title = _title(text, proposal_dir.name)
    text_ref = blobs.put(text, kind="proposal", summary=title[:SUMMARY_CHARS], created_by="cli")
    _index_chunks(text, "proposal", blobs, index, ctx)

    paper_refs = []
    for pdf in sorted((proposal_dir / "papers").glob("*.pdf")):
        paper_text = extract_pdf(pdf)
        if not paper_text.strip():
            log.warning("no text extracted, skipping", extra={"paper": pdf.name})
            continue
        first = next((ln[3:] for ln in paper_text.splitlines() if ln.startswith("## ")), "")
        ref = blobs.put(
            paper_text,
            kind="paper",
            summary=f"{pdf.name}: {first}"[:SUMMARY_CHARS],
            created_by="cli",
        )
        n = _index_chunks(paper_text, pdf.name, blobs, index, ctx)
        log.info("ingested paper", extra={"paper": pdf.name, "chunks": n, "tokens": ref.est_tokens})
        paper_refs.append(ref)

    return Proposal(
        created_by="cli", run_id=ctx.run_id, title=title, text_ref=text_ref, paper_refs=paper_refs
    )
