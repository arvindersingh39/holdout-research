"""Live D93 run on pilot milestone M1. Run only with R2R_LIVE=1 after approval.

`-k design_and_code` (≈ $0.50-2.00, cap $3): retrieve → roadmap → spec → #4 formulation and stubs →
coder sessions with the stub check in Docker → digest → stops at the result gate.
"""

import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from r2r.contracts import HumanDecision
from r2r.graph.deps import RunDeps
from r2r.graph.driver import drive, open_graph
from r2r.graph.gates import GateRequest
from r2r.graph.render import render
from r2r.llm.client import LiteLLMClient
from r2r.settings import load_settings

REPO = Path(__file__).resolve().parents[2]
PILOT = REPO / "examples" / "pilot_mc_pricer"
LATEST = REPO / "runs" / "live-coding.latest"
console = Console(file=sys.stdout, width=110)


class Channel:
    def __init__(self, approve: set[str], pause: set[str]) -> None:
        self.approve, self.pause = approve, pause
        self.gates: list[GateRequest] = []

    def ask(self, req: GateRequest, run_id: str) -> HumanDecision | None:
        self.gates.append(req)
        console.print(Markdown(f"# GATE · {req.title}"))
        if req.gate in self.pause:
            return None
        action = "approve" if req.gate in self.approve else "stop"
        console.print(Markdown(req.markdown))
        return HumanDecision(created_by="human", run_id=run_id, action=action)


def _spend(deps: RunDeps, run_id: str) -> float:
    console.print("\n## Spend by role")
    for role, s in deps.ledger.by_role(run_id).items():
        console.print(
            f"{role:14} calls={s.calls:3} in={s.input_tokens:7} out={s.output_tokens:6} "
            f"cache_read={s.cache_read_tokens:7} ${s.cost_usd:.4f}"
        )
    total = deps.ledger.spent(run_id)
    console.print(f"TOTAL ${total:.4f} · M1 ${deps.ledger.spent(run_id, 'M1'):.4f}")
    return total


def test_design_and_code() -> None:
    settings = load_settings(REPO / "config", REPO / ".env")
    run_id = f"live-coding-{datetime.now():%Y%m%d-%H%M%S}"
    deps = RunDeps.create(
        run_id, PILOT, settings, LiteLLMClient(settings.secrets), REPO, REPO / "prompts"
    )
    LATEST.write_text(run_id)
    ch = Channel(approve={"roadmap"}, pause=set())
    with open_graph(deps) as graph:
        res = drive(graph, run_id, ch, start=True)
    state = res.state
    console.print(Markdown(render(state["math_spec"])))
    console.print(Markdown(render(state["test_result"])))
    console.print(f"gates: {[g.gate for g in ch.gates]} · sessions: {state.get('coder_sessions')}")
    assert ch.gates[-1].gate == "result", [g.gate for g in ch.gates]
    assert state["test_result"].passed
    assert deps.ledger.spent(run_id, "M1") <= settings.budgets.usd_cap_milestone
    _spend(deps, run_id)
