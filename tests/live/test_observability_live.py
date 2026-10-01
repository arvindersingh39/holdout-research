"""Live Langfuse check (D47): one cheap call is traced and tagged. R2R_LIVE=1, after approval."""

import time
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
    auth = (s.langfuse_public_key.get_secret_value(), s.langfuse_secret_key.get_secret_value())
    found = []
    for _ in range(24):  # the OTEL exporter batches; allow up to 2 minutes
        time.sleep(5)
        r = httpx.get(
            f"{s.langfuse_host.rstrip('/')}/api/public/traces",
            params={"tags": f"run:{run_id}"},
            auth=auth,
            timeout=20,
        )
        found = r.json().get("data", []) if r.status_code == 200 else []
        if found:
            break
    print(f"\ntraces for {run_id}: {len(found)}")
    assert found, "no trace arrived in Langfuse"
    assert "role:short_planner" in found[0].get("tags", [])
