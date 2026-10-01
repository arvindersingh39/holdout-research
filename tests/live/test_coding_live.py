"""Live Phase 5 run on pilot milestone M1, in two steps. Run only with R2R_LIVE=1 after approval.

Step 1 (`-k design`, ≈ $0.20-0.45): retrieve → roadmap → spec → #4 design → red before green →
pauses at the tests gate. The run id is saved to runs/live-coding.latest for step 2.
Step 2 (`-k code`, ≈ $0.30-1.50, cap $3): approves the tests gate (after the owner reviewed them)
→ freeze → coder sessions in Docker → digest → stops at the result gate.
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
from r2r.graph.render import render, render_files
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
        if req.gate != "tests":
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


def test_design() -> None:
    settings = load_settings(REPO / "config", REPO / ".env")
    run_id = f"live-coding-{datetime.now():%Y%m%d-%H%M%S}"
    deps = RunDeps.create(
        run_id, PILOT, settings, LiteLLMClient(settings.secrets), REPO, REPO / "prompts"
    )
    LATEST.write_text(run_id)
    ch = Channel(approve={"roadmap"}, pause={"tests"})
    with open_graph(deps) as graph:
        res = drive(graph, run_id, ch, start=True)
    state = res.state
    assert res.status == "paused" and ch.gates[-1].gate == "tests", [g.gate for g in ch.gates]
    assert state["redgreen_ok"]
    ms = state["math_spec"]
    console.print(Markdown(render(state["spec"])))
    console.print(Markdown(render(ms)))
    console.print(Markdown(render_files(deps.workspace().root, [*ms.test_files, *ms.check_files])))
    console.print(f"design attempts that failed: {state.get('design_attempts', 0)}")
    console.print(f"run id: {run_id}  (saved to {LATEST.relative_to(REPO)})")
    assert _spend(deps, run_id) < 1.0


def test_code() -> None:
    settings = load_settings(REPO / "config", REPO / ".env")
    run_id = LATEST.read_text().strip()
    deps = RunDeps.load(run_id, settings, LiteLLMClient(settings.secrets), REPO, REPO / "prompts")
    ch = Channel(approve={"tests"}, pause=set())
    with open_graph(deps) as graph:
        res = drive(graph, run_id, ch, start=False)
    state = res.state
    console.print(
        f"gates: {[g.gate for g in ch.gates]} · coder sessions: "
        f"{state.get('coder_sessions')} · blocked tamper attempts: "
        f"{state.get('tamper_attempts', 0)}"
    )
    console.print(Markdown(render(state["test_result"])))
    assert ch.gates[-1].gate == "result", [g.gate for g in ch.gates]
    assert state["test_result"].passed
    assert deps.ledger.spent(run_id, "M1") <= settings.budgets.usd_cap_milestone
    _spend(deps, run_id)
