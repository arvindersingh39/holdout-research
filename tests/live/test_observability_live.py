"""Live Langfuse check (D47): one cheap call is traced and tagged. R2R_LIVE=1, after approval."""

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import BaseModel

from r2r.llm.client import LiteLLMClient
from r2r.llm.gateway import CallContext, call_structured
from r2r.settings import load_settings
from r2r.store.ledger import Ledger

REPO = Path(__file__).resolve().parents[2]


class Question(BaseModel):
    text: str


class Answer(BaseModel):
    answer: int


def test_call_is_traced_in_langfuse(tmp_path: Path) -> None:
    settings = load_settings(REPO / "config", REPO / ".env")
    s = settings.secrets
    client = LiteLLMClient(s)
    assert client.tracing, "Langfuse keys missing in .env"
    run_id = f"live-trace-{uuid4().hex[:8]}"
    ctx = CallContext(
        run_id=run_id,
        milestone_id=None,
        settings=settings,
        ledger=Ledger(tmp_path / "l.sqlite"),
        client=client,
        prompts_dir=REPO / "tests" / "live" / "prompts",
    )
    assert (
        call_structured("short_planner", "echo", Question(text="2 + 2?"), Answer, ctx).answer == 4
    )
    # New Langfuse orgs (after 2026-09-16) only have the v2 observations API; GET /traces is 410.
    auth = (s.langfuse_public_key.get_secret_value(), s.langfuse_secret_key.get_secret_value())
    since = datetime.now(UTC) - timedelta(minutes=10)
    mine: list[dict] = []
    for _ in range(24):  # the exporter batches; allow up to 2 minutes
        time.sleep(5)
        r = httpx.get(
            f"{s.langfuse_host.rstrip('/')}/api/public/v2/observations",
            params={
                "fromStartTime": since.isoformat(),
                "toStartTime": datetime.now(UTC).isoformat(),
            },
            auth=auth,
            timeout=20,
        )
        r.raise_for_status()
        mine = [o for o in r.json().get("data", []) if o.get("sessionId") == run_id]
        if mine:
            break
    print(f"\nobservations for session {run_id}: {len(mine)}")
    assert mine, "no observation arrived in Langfuse"
    assert mine[0]["name"] == "short_planner" and mine[0]["type"] == "GENERATION"
