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


def make_sandbox_factory():
    """None means the Docker sandbox. The framework's own tests replace this (D29)."""
    return None


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


# --- the pipeline: run, resume, status, show ----------------------------------------------

SHOWABLE = ("brief", "roadmap", "spec", "verdict", "math_spec", "test_result", "digest")
RunsBase = Annotated[Path | None, typer.Option(help="Where runs/ lives (default: repo)")]


def _deps(run_id: str, runs_base: Path | None, proposal: Path | None = None):
    from r2r.graph.deps import RunDeps
    from r2r.log import configure_logging

    root = project_root()
    settings = load_settings(root / "config", root / ".env")
    configure_logging("WARNING", settings.secrets)
    base = runs_base or root
    if proposal is not None:
        return RunDeps.create(
            run_id, proposal, settings, make_client(settings), base, root / "prompts"
        )
    try:
        return RunDeps.load(
            run_id,
            settings,
            make_client(settings),
            base,
            root / "prompts",
            sandbox_factory=make_sandbox_factory(),
        )
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(2) from None


def make_channel(settings: Settings):
    """The gate channel from budgets.yaml. WhatsApp arrives in Phase 7; until then, terminal."""
    from r2r.graph.gates import TerminalChannel

    if settings.budgets.gates.channel == "whatsapp":
        console.print("[yellow]WhatsApp gates arrive in Phase 7; using the terminal for now.[/]")
    return TerminalChannel(console=console)


def _drive(deps, start: bool) -> None:
    from r2r.graph.driver import drive, open_graph
    from r2r.llm.gateway import BudgetExceeded, ContractViolation

    with open_graph(deps) as graph:
        try:
            result = drive(graph, deps.run_id, make_channel(deps.settings), start=start)
        except (BudgetExceeded, ContractViolation) as e:
            console.print(f"[red]{type(e).__name__}:[/] {e}")
            console.print(f"Progress is saved. Fix the cause, then `r2r resume {deps.run_id}`.")
            raise typer.Exit(1) from None
    spent = deps.ledger.spent(deps.run_id)
    console.print(f"Run {deps.run_id}: [bold]{result.status}[/] · spent ${spent:.4f}")


@app.command()
def run(
    proposal: Annotated[Path, typer.Option(help="Folder with proposal.md and optional papers/")],
    run_id: Annotated[str | None, typer.Option(help="Run id (default: folder name + time)")] = None,
    runs_base: RunsBase = None,
) -> None:
    """Start a run: retrieve, plan, and stop at every human gate."""
    if not (proposal / "proposal.md").is_file():
        console.print(f"[red]{proposal}/proposal.md not found[/]")
        raise typer.Exit(2)
    run_id = run_id or f"{proposal.resolve().name}-{datetime.now():%Y%m%d-%H%M%S}"
    _drive(_deps(run_id, runs_base, proposal), start=True)


@app.command()
def resume(run_id: str, runs_base: RunsBase = None) -> None:
    """Continue a run from its last checkpoint (for example a gate you left pending)."""
    _drive(_deps(run_id, runs_base), start=False)


@app.command()
def status(run_id: str, runs_base: RunsBase = None) -> None:
    """Where a run is: pending gate or next step, roadmap, current milestone, spend."""
    from r2r.graph.driver import config, open_graph, pending_gate
    from r2r.graph.routing import next_milestone

    deps = _deps(run_id, runs_base)
    with open_graph(deps) as graph:
        snap = graph.get_state(config(run_id))
        gate = pending_gate(graph, run_id)
    values = snap.values
    console.print(f"Run {run_id} · status: {values.get('status', 'not started')}")
    if gate:
        console.print(f"Waiting at gate: {gate.gate} · {gate.title}")
    elif snap.next:
        console.print(f"Next step: {', '.join(snap.next)}")
    rm = values.get("roadmap")
    if rm is not None:
        m = next_milestone(rm)
        done = sum(x.status == "done" for x in rm.milestones)
        console.print(
            f"Roadmap v{rm.version}: {done}/{len(rm.milestones)} milestones done"
            + (f" · current {m.id} {m.title}" if m else "")
        )
    console.print(f"Spent: ${deps.ledger.spent(run_id):.4f}")


@app.command()
def show(
    run_id: str,
    contract: Annotated[str, typer.Argument(help=f"One of: {', '.join(SHOWABLE)}")],
    runs_base: RunsBase = None,
) -> None:
    """Render a contract from the run's latest state."""
    from rich.markdown import Markdown

    from r2r.graph.driver import config, open_graph
    from r2r.graph.render import render

    if contract not in SHOWABLE:
        console.print(f"[red]Choose one of: {', '.join(SHOWABLE)}[/]")
        raise typer.Exit(2)
    deps = _deps(run_id, runs_base)
    with open_graph(deps) as graph:
        value = graph.get_state(config(run_id)).values.get(contract)
    if value is None:
        console.print(f"No {contract} yet in run {run_id}.")
        raise typer.Exit(1)
    console.print(Markdown(render(value)))


# --- sandbox ----------------------------------------------------------------------------------

sandbox_app = typer.Typer(help="Build and check the Docker sandbox.", no_args_is_help=True)
app.add_typer(sandbox_app, name="sandbox")


@sandbox_app.command("build")
def sandbox_build() -> None:
    """Build the sandbox image (only if sandbox/ changed). The first build takes a few minutes."""
    from r2r.harness.sandbox import ensure_image

    with console.status("Building the sandbox image..."):
        tag = ensure_image()
    console.print(f"Sandbox image ready: [bold]{tag}[/]")


@sandbox_app.command("check")
def sandbox_check() -> None:
    """Smoke-test the sandbox: scientific stack imports, network off, frozen dirs read-only."""
    import tempfile

    from r2r.harness.sandbox import DockerBackend
    from r2r.harness.workspace import Workspace
    from r2r.store.runs import run_paths

    root = project_root()
    limits = load_settings(root / "config", root / ".env").budgets.sandbox
    # Docker Desktop may still hold the read-only mounts for a moment after a container exits,
    # so cleanup of this throwaway workspace must not fail the check.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        ws = Workspace.create(run_paths("sandbox-check", base=Path(tmp), create=True))
        box = DockerBackend(ws, limits)
        checks = {
            "numpy, scipy, numba, sympy import": (
                ["python", "-c", "import numpy, scipy, numba, sympy"],
                True,
            ),
            "network is off": (
                ["python", "-c", "import socket; socket.create_connection(('1.1.1.1', 53), 3)"],
                False,
            ),
            "frozen tests are read-only": (
                ["python", "-c", "open('tests/frozen/x.py', 'w')"],
                False,
            ),
            "runs as non-root": (["sh", "-c", "test $(id -u) -ne 0"], True),
        }
        ok = True
        for name, (cmd, should_succeed) in checks.items():
            passed = (box.run(cmd, 60).exit_code == 0) == should_succeed
            ok &= passed
            console.print(f"{'[green]✓[/]' if passed else '[red]✗[/]'} {name}")
    console.print(f"Sandbox {box.image}: " + ("[green]OK[/]" if ok else "[red]FAILED[/]"))
    if not ok:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
