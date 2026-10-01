"""Builds a context pack for a role within its token budget (Phase 2).

Deterministic: the top-k chunks in rank order, each added only if it still fits. Every chunk
is wrapped in <document> tags because paper text is untrusted data.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from r2r.contracts import BlobRef
from r2r.ingest.documents import wrap_document
from r2r.llm.tokens import estimate_tokens
from r2r.retrieval.vectors import VectorIndex
from r2r.store.blobs import BlobStore

SEPARATOR = "\n\n"


class ContextPack(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    refs: list[BlobRef]
    text: str
    tokens: int


def build_pack(
    query: str, token_budget: int, k: int, index: VectorIndex, blobs: BlobStore
) -> ContextPack:
    refs: list[BlobRef] = []
    parts: list[str] = []
    for hit in index.query(query, k):
        ref = blobs.ref(hit.blob_id)
        doc = wrap_document(blobs.get_text(ref), source=f"chunk:{ref.blob_id[:12]}")
        candidate = SEPARATOR.join([*parts, doc])
        if estimate_tokens(candidate) <= token_budget:
            parts.append(doc)
            refs.append(ref)
    text = SEPARATOR.join(parts)
    return ContextPack(refs=refs, text=text, tokens=estimate_tokens(text))
