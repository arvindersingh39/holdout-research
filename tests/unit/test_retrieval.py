"""Vector index and context packs (fake lexical embeddings, real Chroma, no network)."""

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from r2r.contracts import BlobRef
from r2r.llm.gateway import CallContext
from r2r.llm.tokens import estimate_tokens
from r2r.retrieval.context_pack import build_pack
from r2r.retrieval.vectors import VectorIndex
from r2r.store.blobs import BlobStore

TEXTS = [
    "## Pricing\n\nMonte Carlo option pricing under geometric Brownian motion.",
    "## Variance\n\nAntithetic variates and control variates reduce estimator variance.",
    "## Closed form\n\nThe Black Scholes formula prices European calls exactly.",
    "## Biology\n\nProtein folding uses molecular dynamics simulation.",
]


def _store(
    tmp_path: Path, ctx: CallContext, texts=TEXTS
) -> tuple[VectorIndex, BlobStore, list[BlobRef]]:
    blobs = BlobStore(tmp_path / "blobs")
    refs = [blobs.put(t, kind="chunk", summary=t.split("\n")[0][:80], created_by="retriever")
            for t in texts]  # fmt: skip
    index = VectorIndex(tmp_path / "vectors", ctx)
    index.add(refs, texts)
    return index, blobs, refs


def test_query_ranks_relevant_chunks_first(tmp_path: Path, ctx: CallContext) -> None:
    index, _, refs = _store(tmp_path, ctx)
    hits = index.query("control variates variance", k=2)
    assert hits[0].blob_id == refs[1].blob_id
    assert len(hits) == 2
    assert hits == index.query("control variates variance", k=2)  # deterministic


def test_add_is_idempotent_and_persistent(tmp_path: Path, ctx: CallContext) -> None:
    index, _, refs = _store(tmp_path, ctx)
    index.add(refs[:1], TEXTS[:1])
    assert index.count() == len(TEXTS)
    assert VectorIndex(tmp_path / "vectors", ctx).count() == len(TEXTS)


def test_empty_index(tmp_path: Path, ctx: CallContext) -> None:
    index = VectorIndex(tmp_path / "vectors", ctx)
    assert index.query("anything", k=3) == []
    pack = build_pack("anything", 500, 3, index, BlobStore(tmp_path / "blobs"))
    assert pack.refs == [] and pack.text == "" and pack.tokens == 0


def test_embeddings_are_ledgered(tmp_path: Path, ctx: CallContext) -> None:
    index, _, _ = _store(tmp_path, ctx)
    index.query("pricing", k=1)
    roles = [r.role for r in ctx.ledger.rows("run-test")]
    assert roles == ["embeddings", "embeddings"]  # one for add, one for query


def test_pack_wraps_documents_and_respects_budget(tmp_path: Path, ctx: CallContext) -> None:
    index, blobs, refs = _store(tmp_path, ctx)
    pack = build_pack("monte carlo option pricing", 10_000, k=4, index=index, blobs=blobs)
    assert pack.refs[0] == refs[0]
    assert pack.text.count("<document") == len(pack.refs) == 4
    assert pack.tokens == estimate_tokens(pack.text)

    small = build_pack("monte carlo option pricing", 40, k=4, index=index, blobs=blobs)
    assert small.tokens <= 40
    assert 1 <= len(small.refs) < 4


def test_pack_skips_oversized_but_keeps_later_fits(tmp_path: Path, ctx: CallContext) -> None:
    texts = ["pricing " * 200, "pricing short note"]
    index, blobs, refs = _store(tmp_path, ctx, texts)
    pack = build_pack("pricing", 50, k=2, index=index, blobs=blobs)
    assert pack.refs == [refs[1]]


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(budget=st.integers(min_value=0, max_value=200), k=st.integers(min_value=1, max_value=6))
def test_budget_never_exceeded(tmp_path_factory: pytest.TempPathFactory, ctx: CallContext,
                               budget: int, k: int) -> None:  # fmt: skip
    tmp = tmp_path_factory.mktemp("pack")
    index, blobs, _ = _store(tmp, ctx)
    pack = build_pack("variance pricing formula", budget, k, index, blobs)
    assert pack.tokens <= budget
    assert len(pack.refs) <= k
