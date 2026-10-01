"""Live planning smoke test: the pilot to the first roadmap gate, then to a reviewed spec.

Run only with R2R_LIVE=1 after owner approval. Estimated cost: $0.10-0.30.
Also reports whether Opus's thinking actually ran on the long-planner call.
"""

import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from r2r.contracts import HumanDecision
from r2r.graph.deps import RunDeps
from r2r.graph.driver import drive, open_graph
from r2r.graph.gates import GateRequest
from r2r.graph.render import render
from r2r.llm.client import LiteLLMClient, LLMRequest, LLMResponse
from r2r.llm.tokens import estimate_tokens
from r2r.settings import load_settings

REPO = Path(__file__).resolve().parents[2]
PILOT = REPO / "examples" / "pilot_mc_pricer"
console = Console(file=sys.stdout, width=110)


class RecordingClient(LiteLLMClient):
    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.log: list[tuple[LLMRequest, LLMResponse]] = []

    def complete(self, req: LLMRequest) -> LLMResponse:
        resp = super().complete(req)
        self.log.append((req, resp))
        return resp


class ApproveRoadmapThenStop:
    """Approves the first roadmap; stops at the next gate (result or escalation)."""

    def __init__(self) -> None:
        self.gates: list[GateRequest] = []

    def ask(self, req: GateRequest, run_id: str) -> HumanDecision:
        self.gates.append(req)
        console.print(Markdown(f"# GATE · {req.title}\n\n{req.markdown}"))
        action = "approve" if req.gate == "roadmap" and len(self.gates) == 1 else "stop"
        return HumanDecision(created_by="human", run_id=run_id, action=action)


def test_pilot_to_reviewed_spec(tmp_path: Path) -> None:
    settings = load_settings(REPO / "config", REPO / ".env")
    client = RecordingClient(settings.secrets)
    deps = RunDeps.create("live-plan", PILOT, settings, client, tmp_path, REPO / "prompts")
    channel = ApproveRoadmapThenStop()
    with open_graph(deps) as graph:
        result = drive(graph, "live-plan", channel, start=True)

    state = result.state
    assert channel.gates[0].gate == "roadmap"
    assert channel.gates[-1].gate in ("result", "escalation")
    assert result.status == "stopped"
    for v in [state.get("verdict")]:
        if v is not None:
            console.print(Markdown(render(v)))

    console.print("\n## Spend by role")
    for role, s in deps.ledger.by_role("live-plan").items():
        console.print(
            f"{role:14} calls={s.calls} in={s.input_tokens} out={s.output_tokens} "
            f"cache_read={s.cache_read_tokens} ${s.cost_usd:.4f}"
        )
    total = deps.ledger.spent("live-plan")
    console.print(f"TOTAL ${total:.4f}")
    assert total < 0.50

    # Did thinking run? Compare billed output tokens with the visible answer.
    for req, resp in client.log:
        if req.role == "long_planner":
            visible = estimate_tokens(resp.text or "", req.model)
            console.print(
                f"long_planner: billed out={resp.output_tokens} visible≈{visible} "
                f"reasoning_chars={resp.reasoning_chars} thinking={req.thinking}"
            )
