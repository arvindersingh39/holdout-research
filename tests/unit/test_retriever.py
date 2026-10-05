"""Proposal ingestion and the #1 retriever with FakeLLM and fake embeddings."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from pdfs import INJECTION, make_paper
from samples import paper_card, research_brief

from r2r.agents.retriever import run_retriever
from r2r.contracts import PaperCard, ResearchBrief, set_contract_caps
from r2r.ingest.proposal import ingest_proposal
from r2r.llm.fake import FakeLLM
from r2r.llm.gateway import CallContext
from r2r.retrieval.vectors import VectorIndex
from r2r.store.blobs import BlobStore
from r2r.store.runs import RunPaths, run_paths

BRIEF_DRAFT = research_brief().model_dump_json(
    exclude={"created_by", "run_id", "milestone_id", "paper_cards"}
)


@dataclass
class Env:
    ctx: CallContext
    fake: FakeLLM
    paths: RunPaths
    blobs: BlobStore
    index: VectorIndex
    proposal_dir: Path


@pytest.fixture
def env(tmp_path: Path, ctx: CallContext, fake: FakeLLM) -> Env:
    ctx = CallContext(**{**ctx.__dict__, "milestone_id": None})  # retriever is run-level
    paths = run_paths("run-test", base=tmp_path, create=True)
    pdir = tmp_path / "proposal"
    (pdir / "papers").mkdir(parents=True)
    (pdir / "proposal.md").write_text(
        "# Variance-reduced Monte Carlo pricing\n\nPrice European options fast.\n"
    )
    make_paper(pdir / "papers" / "a.pdf")
    blobs = BlobStore(paths.blobs)
    return Env(ctx, fake, paths, blobs, VectorIndex(paths.vectors, ctx), pdir)


def test_ingest_proposal(env: Env) -> None:
    prop = ingest_proposal(env.proposal_dir, env.blobs, env.index, env.ctx)
    assert prop.title == "Variance-reduced Monte Carlo pricing"
    assert prop.created_by == "cli" and prop.run_id == "run-test"
    assert env.blobs.get_text(prop.text_ref).startswith("# Variance-reduced")
    assert len(prop.paper_refs) == 1 and prop.paper_refs[0].kind == "paper"
    assert "a.pdf" in prop.paper_refs[0].summary
    assert env.index.count() >= 2  # paper chunks plus proposal chunks


def test_ingest_requires_proposal_md(env: Env) -> None:
    (env.proposal_dir / "proposal.md").unlink()
    with pytest.raises(FileNotFoundError):
        ingest_proposal(env.proposal_dir, env.blobs, env.index, env.ctx)


def _run(env: Env):
    prop = ingest_proposal(env.proposal_dir, env.blobs, env.index, env.ctx)
    env.fake.script("retriever", paper_card(), BRIEF_DRAFT)
    return prop, run_retriever(prop, env.blobs, env.paths, env.ctx)


def test_brief_built_from_cards(env: Env) -> None:
    _, res = _run(env)
    assert isinstance(res.brief, ResearchBrief)
    assert res.brief.created_by == "retriever" and res.brief.run_id == "run-test"
    assert res.brief.objective == research_brief().objective
    assert res.brief.paper_cards == res.card_refs
    stored = PaperCard.model_validate_json(env.blobs.get_text(res.card_refs[0]))
    assert stored.citation.key == "glasserman2003"
    assert ResearchBrief.model_validate_json(env.blobs.get_text(res.brief_ref)) == res.brief


def test_refs_bib_written(env: Env) -> None:
    _, res = _run(env)
    assert res.bib_path == env.paths.root / "refs.bib"
    assert "@book{glasserman2003," in res.bib_path.read_text()


def test_brief_never_sees_raw_papers(env: Env) -> None:
    _run(env)
    brief_req = env.fake.calls[1]
    assert brief_req.messages[0]["content"][0]["text"].startswith("# retriever_brief")
    body = brief_req.messages[1]["content"]
    assert "Body sentence" not in body  # paper text only reaches the PaperCard call
    assert "Price European options fast." in body


def _outside_document_tags(text: str, needle: str) -> bool:
    for m in re.finditer(re.escape(needle), text):
        before = text[: m.start()]
        opened = before.rfind("<document")
        closed = before.rfind("</document>")
        if opened == -1 or closed > opened or "</document>" not in text[m.end() :]:
            return True
    return False


def test_injection_only_inside_document_tags(env: Env) -> None:
    _run(env)
    seen = False
    for req in env.fake.calls:
        for msg in req.messages:
            content = msg["content"]
            text = content if isinstance(content, str) else content[0]["text"]
            if INJECTION in text:
                seen = True
                assert not _outside_document_tags(text, INJECTION)
    assert seen, "the injection line should reach the PaperCard call as data"


def test_every_call_is_ledgered(env: Env) -> None:
    _run(env)
    roles = [r.role for r in env.ctx.ledger.rows("run-test")]
    assert roles.count("retriever") == 2
    assert "embeddings" in roles


def test_brief_cap_enforced(env: Env) -> None:
    """Over the cap once papers are attached: one repair, then ContractViolation (never a
    brief over its cap)."""
    from r2r.llm.gateway import ContractViolation

    prop = ingest_proposal(env.proposal_dir, env.blobs, env.index, env.ctx)
    env.fake.script("retriever", paper_card(), BRIEF_DRAFT, BRIEF_DRAFT)
    set_contract_caps({"ResearchBrief": 50})
    with pytest.raises(ContractViolation, match="cap is 50"):
        run_retriever(prop, env.blobs, env.paths, env.ctx)
    briefs = [
        r
        for r in env.fake.calls
        if r.messages[0]["content"][0]["text"].startswith("# retriever_brief")
    ]
    assert len(briefs) == 2  # original + one repair


def test_no_papers(env: Env) -> None:
    (env.proposal_dir / "papers" / "a.pdf").unlink()
    prop = ingest_proposal(env.proposal_dir, env.blobs, env.index, env.ctx)
    env.fake.script("retriever", BRIEF_DRAFT)
    res = run_retriever(prop, env.blobs, env.paths, env.ctx)
    assert res.brief.paper_cards == [] and res.card_refs == []
    assert res.bib_path.read_text() == ""


def test_benchmarks_are_quote_checked(env: Env) -> None:
    """D93: the brief's experimental results are checked against the proposal by code."""
    (env.proposal_dir / "proposal.md").write_text(
        "# Variance-reduced Monte Carlo pricing\n\nThe control variate cuts the standard error "
        "to 0.0123 at 10^5 paths.\n"
    )
    prop = ingest_proposal(env.proposal_dir, env.blobs, env.index, env.ctx)
    bench = {
        "quantity": "standard error",
        "units": "",
        "tolerance": 0.00005,
        "conditions": "10^5 paths",
        "source": "proposal",
        "verified": True,  # the model's claim is ignored: code decides
    }
    draft = json.loads(BRIEF_DRAFT) | {
        "benchmarks": [
            bench | {"id": "BM1", "value": 0.0123, "quote": "cuts the standard error to 0.0123"},
            bench | {"id": "BM2", "value": 0.5, "quote": "cuts the standard error to 0.0123"},
        ]
    }
    env.fake.script("retriever", paper_card(), json.dumps(draft))
    res = run_retriever(prop, env.blobs, env.paths, env.ctx)
    assert [b.verified for b in res.brief.benchmarks] == [True, False]
