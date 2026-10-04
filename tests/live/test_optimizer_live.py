"""Live simplifier smoke test (D85; Gemini Flash + Docker). Run only with R2R_LIVE=1 after
approval.

(a) slow_project: at least one simplification must be accepted (the loop is a closed form).
(b) the owner's pilot run, M1: the loop must stop by rule with a logged reason.
Estimated cost $0.02-0.05.
"""

import shutil
import sys
from pathlib import Path

from rich.console import Console

from r2r.agents.optimizer import notes, run_optimizer
from r2r.contracts import BenchSpec, MathSpec, OptimizationOutcome, OptimizationPatch
from r2r.graph.deps import RunDeps
from r2r.graph.driver import config, open_graph
from r2r.harness.frozen import freeze
from r2r.harness.gitops import Git
from r2r.harness.optimize import Baseline, baseline, source_listing, try_patch
from r2r.harness.sandbox import DockerBackend
from r2r.harness.stoprule import decide
from r2r.harness.workspace import Workspace
from r2r.llm.client import LiteLLMClient
from r2r.llm.gateway import CallContext, ContractViolation
from r2r.settings import load_settings
from r2r.store.ledger import Ledger
from r2r.store.runs import run_paths

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "slow_project"
PILOT_RUN = "live-coding-20261001-131008"
console = Console(file=sys.stdout, width=110)


def optimize_until_stop(ws, box, ms: MathSpec, ctx: CallContext) -> tuple[list, str]:
    """The same steps as the graph's opt_baseline/optimize nodes, in a plain loop."""
    cfg = ctx.settings.budgets
    entry = ms.bench.entrypoint
    b = baseline(ws, box, entry, cfg, run_id=ctx.run_id, milestone_id=ctx.milestone_id)
    console.print(f"baseline {b.runtime_s:.4g} s, sloc {b.sloc}, complexity {b.complexity}")
    current = b
    outcomes: list[OptimizationOutcome] = []
    patches: list[OptimizationPatch | None] = []
    action, reason = decide([], cfg)
    while action == "continue":
        try:
            patch = run_optimizer(
                source_listing(ws),
                current.complexity,
                current.runtime_s,
                ms,
                notes(patches, outcomes),
                ctx,
            )
        except ContractViolation as e:  # same as the graph node: a failed attempt, not a crash
            out = OptimizationOutcome(
                created_by="harness",
                run_id=ctx.run_id,
                milestone_id=ctx.milestone_id,
                attempt=len(outcomes) + 1,
                accepted=False,
                reason=f"rejected: no valid patch ({str(e)[:150]})",
                runtime_before_s=current.runtime_s,
                sloc_before=current.sloc,
                complexity_before=current.complexity,
            )
            console.print(f"#{out.attempt} no valid patch → {out.reason}")
            outcomes.append(out)
            patches.append(None)
            action, reason = decide(outcomes, cfg)
            continue
        out = try_patch(
            ws,
            box,
            Git(ws),
            patch,
            current,
            len(outcomes) + 1,
            entry,
            cfg,
            run_id=ctx.run_id,
            milestone_id=ctx.milestone_id,
        )
        console.print(f"#{out.attempt} {patch.technique}: {patch.rationale}\n   → {out.reason}")
        if out.accepted:
            current = Baseline(
                out.runtime_after_s, 0.0, out.sloc_after, b.bench_hash, out.complexity_after
            )
        outcomes.append(out)
        patches.append(patch)
        action, reason = decide(outcomes, cfg)
    console.print(f"STOP: {reason}")
    return outcomes, reason


def test_slow_project_simplification_accepted(tmp_path: Path) -> None:
    settings = load_settings(REPO / "config", REPO / ".env")
    ws = Workspace.create(run_paths("live-opt", base=tmp_path, create=True))
    for sub in ("src", "bench", "tests/frozen", "checks"):
        shutil.copytree(FIXTURE / sub, ws.root / sub, dirs_exist_ok=True)
    t = ws.root / "tests/frozen/test_core.py"
    t.write_text(t.read_text().split("def test_AC2__deliberately_wrong")[0])
    freeze(ws)
    Git(ws).commit("frozen")
    ms = MathSpec(
        created_by="math_designer",
        run_id="live-opt",
        milestone_id="M1",
        formulation="sum_{i<n} i^2 = (n-1)n(2n-1)/6",
        algorithm="sum of squares",
        signatures=[
            "slow_sum(n: int) -> int",
            "closed_form(n: int) -> int",
            "compute(n: int = 200000) -> int",
        ],
        tolerances={"exact": 0.0},
        test_files=["tests/frozen/test_core.py"],
        bench=BenchSpec(entrypoint="bench/bench_main.py", problem_size="n=300000"),
    )
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ctx = CallContext(
        run_id="live-opt",
        milestone_id="M1",
        settings=settings,
        ledger=ledger,
        client=LiteLLMClient(settings.secrets),
        prompts_dir=REPO / "prompts",
    )
    outcomes, reason = optimize_until_stop(ws, DockerBackend(ws, settings.budgets.sandbox), ms, ctx)
    console.print(f"cost ${ledger.spent('live-opt'):.4f}")
    assert any(o.accepted for o in outcomes)
    assert reason


def test_pilot_m1_stops_by_rule() -> None:
    settings = load_settings(REPO / "config", REPO / ".env")
    deps = RunDeps.load(
        PILOT_RUN, settings, LiteLLMClient(settings.secrets), REPO, REPO / "prompts"
    )
    with open_graph(deps) as graph:
        ms = graph.get_state(config(PILOT_RUN)).values["math_spec"]
    ws = deps.workspace()
    before = deps.ledger.spent(PILOT_RUN)
    outcomes, reason = optimize_until_stop(ws, deps.sandbox(), ms, deps.ctx("M1"))
    console.print(f"cost ${deps.ledger.spent(PILOT_RUN) - before:.4f}")
    assert reason and len(outcomes) <= settings.budgets.optimizer_max_attempts
