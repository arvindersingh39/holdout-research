"""Embedded Chroma store, one collection per run (Phase 2).

Embeddings are computed through `gateway.embed` (ledgered) and handed to Chroma explicitly;
Chroma never embeds anything itself. Telemetry is off (D19).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings

from r2r.contracts import BlobRef
from r2r.llm.gateway import CallContext, embed

COLLECTION = "chunks"


@dataclass(frozen=True)
class Hit:
    blob_id: str
    distance: float


class VectorIndex:
    def __init__(self, path: Path, ctx: CallContext) -> None:
        self.ctx = ctx
        Path(path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
        )
        self._col = self._client.get_or_create_collection(
            COLLECTION, embedding_function=None, metadata={"hnsw:space": "cosine"}
        )

    def add(self, refs: list[BlobRef], texts: list[str]) -> None:
        """Index chunks by blob id; re-adding the same blob is a no-op."""
        if len(refs) != len(texts):
            raise ValueError("refs and texts must have the same length")
        existing = set(self._col.get(ids=[r.blob_id for r in refs])["ids"]) if refs else set()
        new = [(r, t) for r, t in zip(refs, texts, strict=True) if r.blob_id not in existing]
        if not new:
            return
        self._col.add(
            ids=[r.blob_id for r, _ in new],
            embeddings=embed([t for _, t in new], self.ctx),
            metadatas=[{"kind": r.kind, "summary": r.summary} for r, _ in new],
        )

    def query(self, text: str, k: int) -> list[Hit]:
        n = min(k, self.count())
        if n <= 0:
            return []
        res = self._col.query(query_embeddings=embed([text], self.ctx), n_results=n)
        hits = [Hit(i, float(d)) for i, d in zip(res["ids"][0], res["distances"][0], strict=True)]
        return sorted(hits, key=lambda h: (round(h.distance, 6), h.blob_id))

    def count(self) -> int:
        return self._col.count()
