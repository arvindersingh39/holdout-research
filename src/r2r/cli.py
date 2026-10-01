"""Typer CLI: run, resume, status, show, costs, ingest, bot (Phase 3+)."""

import typer

from r2r import __version__

app = typer.Typer(no_args_is_help=True, help="r2r: research proposal to publish-ready results.")


@app.command()
def version() -> None:
    """Show the r2r version."""
    typer.echo(__version__)
