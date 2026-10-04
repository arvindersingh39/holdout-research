"""Typer CLI: run, resume, status, show, costs, ingest, bot (Phase 3+)."""

from __future__ import annotations

import json
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


def project_settings() -> Settings:
    """Config and secrets for the CLI. Tests replace this so the real `.env` is never read."""
    root = project_root()
    return load_settings(root / "config", root / ".env")


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
    settings = project_settings()
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

SHOWABLE = (
    "brief",
    "roadmap",
    "spec",
    "verdict",
    "math_spec",
    "test_result",
    "digest",
    "oversight",
    "decisions",
)
RunsBase = Annotated[Path | None, typer.Option(help="Where runs/ lives (default: repo)")]
ChannelOpt = Annotated[
    str | None, typer.Option(help="Gate channel: terminal or whatsapp (default: budgets.yaml)")
]


def _deps(run_id: str, runs_base: Path | None, proposal: Path | None = None):
    from r2r.graph.deps import RunDeps
    from r2r.log import configure_logging

    root = project_root()
    settings = project_settings()
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


def _whatsapp_ready(settings: Settings) -> bool:
    s = settings.secrets
    return bool(s.whatsapp_access_token and s.whatsapp_phone_number_id and s.whatsapp_owner_number)


def _whatsapp_parts(settings: Settings, root: Path):
    from r2r.whatsapp.client import GraphClient
    from r2r.whatsapp.store import GateStore

    s = settings.secrets
    store = GateStore(root / "runs" / "whatsapp.sqlite")
    client = GraphClient(s.whatsapp_access_token.get_secret_value(), s.whatsapp_phone_number_id)
    gates = settings.budgets.gates.whatsapp.model_dump() | {
        "remind_after_h": settings.budgets.gates.remind_after_h,
        "max_reminders": settings.budgets.gates.max_reminders,
        "full_text": settings.budgets.gates.full_text,
    }
    return store, client, s.whatsapp_owner_number, gates


def make_channel(settings: Settings, override: str | None = None):
    """budgets.yaml `gates.channel` (D2), or `--channel`. WhatsApp needs its keys in .env."""
    from r2r.graph.gates import TerminalChannel

    wanted = override or settings.budgets.gates.channel
    if wanted == "whatsapp":
        if _whatsapp_ready(settings):
            from r2r.whatsapp.channel import WhatsAppChannel

            store, client, owner, gates = _whatsapp_parts(settings, project_root())
            return WhatsAppChannel(store, client, owner, gates)
        console.print("[yellow]WhatsApp keys are not set in .env; using the terminal.[/]")
    return TerminalChannel(console=console)


AutopilotOpt = Annotated[
    bool, typer.Option("--autopilot", help="Decide routine gates automatically (D76)")
]


def _with_autopilot(deps, on: bool):
    """--autopilot for this invocation: routine gates are decided by plain-Python rules."""
    if on:
        b = deps.settings.budgets
        ap = b.autopilot.model_copy(update={"enabled": True})
        deps.settings = deps.settings.model_copy(
            update={"budgets": b.model_copy(update={"autopilot": ap})}
        )
        console.print("[bold]Autopilot on:[/] routine gates are decided by rules and logged.")
    return deps


def _drive(deps, start: bool, channel: str | None = None) -> None:
    from r2r.graph.driver import drive, open_graph
    from r2r.llm.gateway import BudgetExceeded, ContractViolation

    with open_graph(deps) as graph:
        try:
            result = drive(graph, deps.run_id, make_channel(deps.settings, channel), start=start)
        except (BudgetExceeded, ContractViolation) as e:
            console.print(f"[red]{type(e).__name__}:[/] {e}")
            console.print(f"Progress is saved. Fix the cause, then `r2r resume {deps.run_id}`.")
            raise typer.Exit(1) from None
    spent = deps.ledger.spent(deps.run_id)
    console.print(f"Run {deps.run_id}: [bold]{result.status}[/] · spent ${spent:.4f}")
    if result.status == "paused":
        console.print(f"Waiting at a gate. Continue with `r2r resume {deps.run_id}`.")


@app.command()
def run(
    proposal: Annotated[Path, typer.Option(help="Folder with proposal.md and optional papers/")],
    run_id: Annotated[str | None, typer.Option(help="Run id (default: folder name + time)")] = None,
    runs_base: RunsBase = None,
    channel: ChannelOpt = None,
    autopilot: AutopilotOpt = False,
) -> None:
    """Start a run: retrieve, plan, and stop at every human gate (or routine ones on autopilot)."""
    if not (proposal / "proposal.md").is_file():
        console.print(f"[red]{proposal}/proposal.md not found[/]")
        raise typer.Exit(2)
    run_id = run_id or f"{proposal.resolve().name}-{datetime.now():%Y%m%d-%H%M%S}"
    deps = _with_autopilot(_deps(run_id, runs_base, proposal), autopilot)
    _drive(deps, start=True, channel=channel)


@app.command()
def resume(
    run_id: str,
    runs_base: RunsBase = None,
    channel: ChannelOpt = None,
    autopilot: AutopilotOpt = False,
) -> None:
    """Continue a run from its last checkpoint (for example a gate you left pending)."""
    _drive(_with_autopilot(_deps(run_id, runs_base), autopilot), start=False, channel=channel)


@app.command("extend")
def extend_cmd(
    run_id: str,
    proposal: Annotated[Path, typer.Option(help="Folder with the new proposal.md (and papers/)")],
    runs_base: RunsBase = None,
    channel: ChannelOpt = None,
    autopilot: AutopilotOpt = False,
) -> None:
    """Continue a finished run with a new proposal: new milestones on the same workspace (D89)."""
    from r2r.graph.extend import ExtendError, extend

    if not (proposal / "proposal.md").is_file():
        console.print(f"[red]{proposal}/proposal.md not found[/]")
        raise typer.Exit(2)
    deps = _with_autopilot(_deps(run_id, runs_base), autopilot)
    try:
        deps = extend(deps, proposal)
    except ExtendError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(2) from None
    console.print(
        f"Extending {run_id} with {proposal.resolve()}: the long planner adds milestones."
    )
    _drive(deps, start=False, channel=channel)


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
    if contract == "decisions":  # Phase 7d: what autopilot decided, and why
        log_file = deps.paths.root / "decisions.jsonl"
        if not log_file.is_file():
            console.print("No automatic decisions in this run.")
            return
        for line in log_file.read_text().splitlines():
            e = json.loads(line)
            ms = e.get("milestone") or "run"
            console.print(f"{e['ts']}  {ms:<4} {e['gate']:<12} {e['action']:<9} {e['note']}")
        return
    with open_graph(deps) as graph:
        values = graph.get_state(config(run_id)).values
    if contract == "oversight":  # Phase 7c: active briefings and this milestone's flags
        from r2r.graph.nodes import render_oversight

        md = render_oversight(values, flags_shown=1000, full=True)
        console.print(Markdown(md) if md else "No overseer briefings or flags yet.")
        return
    value = values.get(contract)
    if value is None:
        console.print(f"No {contract} yet in run {run_id}.")
        raise typer.Exit(1)
    console.print(Markdown(render(value)))


@app.command()
def publish(
    run_id: str,
    mode: Annotated[
        str | None, typer.Option(help="paper or package (default: both, from budgets.yaml)")
    ] = None,
    note: Annotated[str | None, typer.Option(help="A revision request for the writer")] = None,
    runs_base: RunsBase = None,
) -> None:
    """Build the paper and/or package from the run's finished milestones (calls the writer)."""
    from rich.markdown import Markdown

    from r2r.graph.driver import open_graph
    from r2r.publish.collect import NothingToPublish, collect_run
    from r2r.publish.run import run_publish

    deps = _deps(run_id, runs_base)
    modes = [mode] if mode else deps.settings.budgets.publish.modes
    if not set(modes) <= {"paper", "package"}:
        console.print("[red]--mode must be paper or package[/]")
        raise typer.Exit(2)
    with open_graph(deps) as graph:
        try:
            record = collect_run(graph, run_id)
        except NothingToPublish as e:
            console.print(f"[red]{e}[/]")
            raise typer.Exit(1) from e
    with console.status("Publishing..."):
        _, md = run_publish(
            record,
            deps.workspace(),
            deps.paths.root / "publish",
            deps.sandbox_for,
            deps.ctx(),
            deps.blobs,
            modes,
            note=note,
        )
    console.print(Markdown(md))


@app.command()
def costs(run_id: str, runs_base: RunsBase = None) -> None:
    """Spend by role, model and milestone, with cache-read share."""
    from rich.table import Table

    deps = _deps(run_id, runs_base)
    led = deps.ledger

    def table(title: str, groups: dict) -> Table:
        tb = Table(title=title)
        for col in ("", "calls", "input", "output", "cache read", "cost $"):
            tb.add_column(col, justify="left" if col == "" else "right")
        for name, s in groups.items():
            share = f"{s.cache_read_tokens / s.input_tokens:.0%}" if s.input_tokens else "-"
            tb.add_row(
                name,
                str(s.calls),
                f"{s.input_tokens:,}",
                f"{s.output_tokens:,}",
                share,
                f"{s.cost_usd:.4f}",
            )
        return tb

    console.print(table(f"Run {run_id}: by role", led.by_role(run_id)))
    console.print(table("By model", led.by_model(run_id)))
    ms = Table(title="By milestone")
    ms.add_column("milestone")
    ms.add_column("cost $", justify="right")
    for m, c in led.by_milestone(run_id).items():
        ms.add_row(m or "run-level", f"{c:.4f}")
    console.print(ms)
    console.print(
        f"[bold]Total ${led.spent(run_id):.4f}[/]  ·  compare: console.anthropic.com "
        "(Usage) and openrouter.ai/activity"
    )


@app.command()
def bot(
    port: Annotated[int, typer.Option(help="Local port your tunnel forwards to")] = 8787,
) -> None:
    """WhatsApp gate bot: webhook receiver + reminders. Decisions start `r2r resume RUN_ID`."""
    import subprocess
    import sys
    import threading
    import time as _time

    import uvicorn

    from r2r.log import configure_logging
    from r2r.whatsapp.bot import Bot
    from r2r.whatsapp.webhook import create_app

    root = project_root()
    settings = project_settings()
    configure_logging("INFO", settings.secrets)
    s = settings.secrets
    if not (_whatsapp_ready(settings) and s.whatsapp_app_secret and s.whatsapp_verify_token):
        console.print("[red]Set the WHATSAPP_* values in .env first (PLAN §13).[/]")
        raise typer.Exit(2)
    store, client, owner, gates = _whatsapp_parts(settings, root)

    def spawn_resume(run_id: str) -> None:
        log_file = (root / "runs" / run_id / "resume.log").open("a")
        subprocess.Popen(
            [sys.executable, "-m", "r2r.cli", "resume", run_id],
            cwd=root,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    the_bot = Bot(store, client, owner, gates, spawn_resume)

    def reminders() -> None:
        while True:
            _time.sleep(60)
            try:
                the_bot.remind()
            except Exception as e:  # keep the bot alive; the next tick retries
                console.print(f"[yellow]reminder failed: {type(e).__name__}[/]")

    threading.Thread(target=reminders, daemon=True).start()
    app_ = create_app(
        the_bot,
        s.whatsapp_app_secret.get_secret_value(),
        s.whatsapp_verify_token.get_secret_value(),
    )
    console.print(
        f"r2r bot listening on http://127.0.0.1:{port}/webhook "
        f"(public: {s.public_webhook_url or 'set PUBLIC_WEBHOOK_URL'})"
    )
    uvicorn.run(app_, host="127.0.0.1", port=port, log_level="warning")


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

    limits = project_settings().budgets.sandbox
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
