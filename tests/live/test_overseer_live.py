"""Live overseer (Opus) at the `plan` hook. R2R_LIVE=1, after approval. One call, ≈ $0.05-0.1.

The roadmap repeats pilot-2's M3 criterion, which quotes the Onsager-Yang formula but states the
wrong value (0.9862; the formula gives 0.95686 at T=1.8). Pass = the overseer flags it, citing it.
"""

from pathlib import Path

from r2r.agents.overseer import collect_evidence, run_overseer
from r2r.contracts import Criterion, Milestone, Roadmap
from r2r.llm.client import LiteLLMClient
from r2r.llm.gateway import CallContext
from r2r.settings import load_settings
from r2r.store.ledger import Ledger

REPO = Path(__file__).resolve().parents[2]
WRONG = (
    "Function for Onsager-Yang spontaneous magnetization (1-sinh(2/T)^-4)^(1/8) returns 0 for "
    "T>=T_c and ≈0.9862 (to 1e-3) at T=1.8."
)


def test_overseer_flags_a_wrong_expected_value(tmp_path: Path) -> None:
    settings = load_settings(REPO / "config", REPO / ".env")
    ctx = CallContext(
        run_id="live-overseer",
        milestone_id=None,
        settings=settings,
        ledger=Ledger(tmp_path / "l.sqlite"),
        client=LiteLLMClient(settings.secrets),
        prompts_dir=REPO / "prompts",
    )
    rm = Roadmap(
        created_by="long_planner",
        run_id="live-overseer",
        version=1,
        goal="Monte Carlo study of the 2D Ising model (J=1, k_B=1, periodic LxL lattice).",
        milestones=[
            Milestone(
                id="M1",
                title="Exact 4x4 enumeration and Metropolis",
                acceptance_criteria=[
                    Criterion(id="AC1", text="Exact enumeration of all 2^16 states of 4x4."),
                ],
            ),
            Milestone(
                id="M3",
                title="Magnetization vs Onsager-Yang exact solution",
                acceptance_criteria=[Criterion(id="AC1", text=WRONG)],
                depends_on=["M1"],
            ),
        ],
        rationale="First cycle.",
    )
    evidence = collect_evidence({"roadmap": rm}, "plan")
    wrong_id = next(e.id for e in evidence if "0.9862" in e.text)
    rep = run_overseer("plan", rm.goal, None, evidence, {}, ctx)
    print(rep.model_dump_json(indent=2))
    print("cost", sum(r.cost_usd for r in ctx.ledger.rows("live-overseer")))
    hits = [f for f in rep.flags if wrong_id in f.evidence and f.severity in ("high", "warn")]
    assert hits, "overseer did not flag the wrong Onsager-Yang value"
    assert any("0.95" in f.text for f in hits)
