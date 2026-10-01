"""Live retriever smoke tests: real Gemini Flash calls and the real local embedding model.

Run only with R2R_LIVE=1 after owner approval. Estimated cost: under $0.02.
The first run downloads Chroma's MiniLM ONNX model (~80 MB, free).
"""

import shutil
import sys
from pathlib import Path

from typer.testing import CliRunner

import r2r.cli as cli
from r2r.contracts import PaperCard
from r2r.settings import load_settings
from r2r.store.blobs import BlobStore
from r2r.store.ledger import Ledger

sys.path.insert(0, str(Path(__file__).parents[1] / "unit"))
from pdfs import make_paper

REPO = Path(__file__).resolve().parents[2]
PILOT = REPO / "examples" / "pilot_mc_pricer"


def _ingest(proposal: Path, run_id: str, base: Path) -> str:
    result = CliRunner().invoke(
        cli.app,
        ["ingest", "--proposal", str(proposal), "--run-id", run_id, "--runs-base", str(base)],
    )
    print("\n" + result.output)
    assert result.exit_code == 0, result.output
    return result.output


def _check_run(base: Path, run_id: str) -> Ledger:
    caps = load_settings(REPO / "config", None).budgets.contract_token_caps
    run = base / "runs" / run_id
    ledger = Ledger(run / "ledger.sqlite")
    roles = {r.role for r in ledger.rows(run_id)}
    assert {"retriever", "embeddings"} <= roles
    assert all(r.ok for r in ledger.rows(run_id) if r.role == "embeddings")
    assert ledger.spent(run_id) < 0.05
    assert (run / "refs.bib").exists()
    # the brief stored in the blob store stays within its cap
    from r2r.contracts import ResearchBrief

    blobs = BlobStore(run / "blobs")
    briefs = [p for p in (run / "blobs").rglob("*") if p.is_file() and p.suffix != ".sqlite"]
    found = [
        ResearchBrief.model_validate_json(p.read_text())
        for p in briefs
        if '"objective"' in p.read_text(errors="ignore")
    ]
    assert len(found) == 1
    assert found[0].token_estimate() <= caps["ResearchBrief"]
    assert blobs.count() > 0
    return ledger


def test_pilot_brief(tmp_path: Path) -> None:
    _ingest(PILOT, "live-pilot", tmp_path)
    _check_run(tmp_path, "live-pilot")
    assert (tmp_path / "runs" / "live-pilot" / "refs.bib").read_text() == ""  # pilot has no papers


def test_pilot_with_generated_paper(tmp_path: Path) -> None:
    pdir = tmp_path / "proposal"
    (pdir / "papers").mkdir(parents=True)
    shutil.copy(PILOT / "proposal.md", pdir / "proposal.md")
    make_paper(pdir / "papers" / "test_paper.pdf")
    _ingest(pdir, "live-paper", tmp_path)
    _check_run(tmp_path, "live-paper")
    run = tmp_path / "runs" / "live-paper"
    bib = (run / "refs.bib").read_text()
    assert bib.count("@") == 1
    cards = [
        p.read_text()
        for p in (run / "blobs").rglob("*")
        if p.is_file() and '"citation"' in p.read_text(errors="ignore")
    ]
    card = PaperCard.model_validate_json(cards[0])
    print(f"PaperCard: {card.citation.key} · {card.citation.title}\nrelevance: {card.relevance}")
    assert "approve every milestone" not in card.method.lower()
