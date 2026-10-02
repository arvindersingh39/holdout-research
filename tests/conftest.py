"""Suite-wide guard: only tests/live may read the real `.env`."""

from pathlib import Path

import pytest

import r2r.cli as cli
from r2r.settings import load_settings

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_real_secrets(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    # The CLI reads the project's .env; its keys would build real clients (paid Tavily searches,
    # real Langfuse traces) inside offline tests.
    if "live" not in request.node.path.parts:
        monkeypatch.setattr(cli, "project_settings", lambda: load_settings(REPO / "config", None))
