"""Typer CLI: run, resume, status, show, costs, ingest, bot (Phase 3+)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from r2r import __version__
from r2r.llm.client import LiteLLMClient, LLMClient
from r2r.settings import Settings, load_settings

app = typer.Typer(no_args_is_help=True, help="r2r: research proposal to publish-ready results.")
console = Console()


def project_root() -> Path:
    """The repo root: the current directory if it has config/, else the source checkout."""
    cwd = Path.cwd()
    return cwd if (cwd / "config").is_dir() else Path(__file__).resolve().parents[2]


def make_client(settings: Settings) -> LLMClient:
    """The real LLM client. Tests replace this with FakeLLM."""
    return LiteLLMClient(settings.secrets)


@app.command()
def version() -> None:
    """Show the r2r version."""
    typer.echo(__version__)


@app.command()
def ingest(
    proposal: Annotated[Path, typer.Option(help="Folder with proposal.md and optional papers/")],
    run_id: Annotated[str | None, typer.Option(help="Run id (default: folder name + time)")] = None,
    runs_base: Annotated[
        Path | None, typer.Option(help="Where runs/ lives (default: repo)")
    ] = None,
) -> None:
    """Ingest a proposal and its papers, then build the ResearchBrief and refs.bib."""
    from r2r.agents.retriever import run_retriever
    from r2r.ingest.proposal import ingest_proposal
    from r2r.llm.gateway import CallContext
    from r2r.log import configure_logging
    from r2r.retrieval.vectors import VectorIndex
    from r2r.store.blobs import BlobStore
    from r2r.store.ledger import Ledger
    from r2r.store.runs import run_paths

    if not (proposal / "proposal.md").is_file():
        console.print(f"[red]{proposal}/proposal.md not found[/]")
        raise typer.Exit(2)
    root = project_root()
    settings = load_settings(root / "config", root / ".env")
    configure_logging("WARNING", settings.secrets)
    run_id = run_id or f"{proposal.resolve().name}-{datetime.now():%Y%m%d-%H%M%S}"
    paths = run_paths(run_id, base=runs_base or root, create=True)
    ctx = CallContext(
        run_id=run_id,
        milestone_id=None,
        settings=settings,
        ledger=Ledger(paths.ledger),
        client=make_client(settings),
        prompts_dir=root / "prompts",
    )
    blobs = BlobStore(paths.blobs)

    with console.status("Ingesting proposal and papers..."):
        prop = ingest_proposal(proposal, blobs, VectorIndex(paths.vectors, ctx), ctx)
    with console.status(f"Running the retriever on {len(prop.paper_refs)} paper(s)..."):
        res = run_retriever(prop, blobs, paths, ctx)

    brief = res.brief
    console.print(f"[bold]{prop.title}[/]  (run {run_id})")
    console.print(f"Objective: {brief.objective}")
    console.print(
        f"Papers: {len(res.card_refs)} · hypotheses: {len(brief.hypotheses)} · "
        f"metrics: {len(brief.success_metrics)} · ~{brief.token_estimate()} tokens"
    )
    console.print(f"Brief blob: {res.brief_ref.blob_id[:12]} · refs.bib: {res.bib_path}")
    console.print(f"Cost: ${ctx.ledger.spent(run_id):.4f}")
