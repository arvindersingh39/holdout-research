"""One simplifier attempt (D85), judged entirely by the harness.

`try_patch` applies the patch on a trial branch, verifies the frozen tests, runs tests, checks and
sanity checks, measures complexity and the benchmark, and keeps (fast-forward main) or reverts by
`stoprule.accept`. The LLM never decides.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from r2r.contracts import OptimizationOutcome, OptimizationPatch
from r2r.harness.bench import BenchFailed, run_bench
from r2r.harness.checks import run_checks
from r2r.harness.complexity import complexity
from r2r.harness.frozen import FrozenTamper
from r2r.harness.gitops import Git, sloc
from r2r.harness.sandbox import SandboxBackend
from r2r.harness.stoprule import accept
from r2r.harness.tests import run_tests
from r2r.harness.workspace import Workspace
from r2r.settings import BudgetsConfig

OPTIMIZER_DIRS = ("src",)  # D38: never bench/ or tests
SOURCE_CHARS = 24_000  # source shown to the simplifier per attempt (~6k tokens)


@dataclass(frozen=True)
class Baseline:
    runtime_s: float
    cv: float
    sloc: int
    bench_hash: str
    complexity: int


def bench_hash(ws: Workspace, entrypoint: str) -> str:
    return hashlib.sha256((ws.root / entrypoint).read_bytes()).hexdigest()


def baseline(
    ws: Workspace,
    box: SandboxBackend,
    entrypoint: str,
    cfg: BudgetsConfig,
    *,
    run_id: str,
    milestone_id: str,
) -> Baseline:
    bench = run_bench(ws, box, entrypoint, cfg.bench_repeats, cfg.sandbox.bench_timeout_s)
    cx = complexity(ws.root / "src")
    return Baseline(bench.median_s, bench.cv, sloc(box), bench_hash(ws, entrypoint), cx)


def source_listing(ws: Workspace, max_chars: int = SOURCE_CHARS) -> str:
    """The package source for the simplifier, largest files first, within `max_chars`."""
    files = sorted(
        (p for p in (ws.root / "src").rglob("*.py") if "__pycache__" not in p.parts),
        key=lambda p: (-p.stat().st_size, p.as_posix()),
    )
    parts, used, omitted = [], 0, []
    for p in files:
        rel = p.relative_to(ws.root).as_posix()
        text = f"### {rel}\n{p.read_text()}\n"
        if used + len(text) > max_chars:
            omitted.append(rel)
            continue
        parts.append(text)
        used += len(text)
    if omitted:
        parts.append(f"(omitted for length: {', '.join(omitted)[:200]})\n")
    return "".join(parts)


def try_patch(
    ws: Workspace,
    box: SandboxBackend,
    git: Git,
    patch: OptimizationPatch,
    before: Baseline,
    attempt: int,
    entrypoint: str,
    cfg: BudgetsConfig,
    *,
    run_id: str,
    milestone_id: str,
) -> OptimizationOutcome:
    """Try `patch` against `before` (the current best). Raises FrozenTamper after reverting."""
    base = git.head()
    branch = f"opt/{milestone_id}-{attempt}"
    git.branch(branch, base)

    def outcome(
        accepted: bool,
        reason: str,
        after: float | None = None,
        sloc_after: int | None = None,
        cx_after: int | None = None,
    ) -> OptimizationOutcome:
        if accepted:
            git.commit(f"{milestone_id}: simplifier attempt {attempt}: {patch.technique}")
            git.checkout("main")
            git.run("merge", "-q", "--ff-only", branch)
        else:
            git.reset_hard(base)
            git.checkout("main")
        git.run("branch", "-D", branch)
        return OptimizationOutcome(
            created_by="harness",
            run_id=run_id,
            milestone_id=milestone_id,
            attempt=attempt,
            accepted=accepted,
            reason=reason[:300],
            runtime_before_s=before.runtime_s,
            runtime_after_s=after,
            sloc_before=before.sloc,
            sloc_after=sloc_after,
            complexity_before=before.complexity,
            complexity_after=cx_after,
        )

    ok, why = git.apply_patch(patch.diff, allowed_dirs=OPTIMIZER_DIRS)
    if not ok:
        return outcome(False, f"rejected: {why}")
    if bench_hash(ws, entrypoint) != before.bench_hash:
        return outcome(False, "rejected: bench file changed since the baseline")
    timeout = cfg.sandbox.test_timeout_s
    try:
        result = run_tests(ws, box, run_id=run_id, milestone_id=milestone_id, timeout_s=timeout)
    except FrozenTamper:
        outcome(False, "rejected: frozen files changed")
        raise
    checks_ok = all(c.passed for c in run_checks(ws, box, timeout))
    if not (result.passed and checks_ok):
        what = "tests" if not result.passed else "checks"
        return outcome(False, f"rejected: {what} failed ({result.n_failed} failing tests)")
    try:
        bench = run_bench(ws, box, entrypoint, cfg.bench_repeats, cfg.sandbox.bench_timeout_s)
    except BenchFailed as e:
        return outcome(False, f"rejected: benchmark failed: {e}")
    sloc_after, cx_after = sloc(box), complexity(ws.root / "src")
    accepted, reason = accept(
        before.runtime_s, bench.median_s, True, before.complexity, cx_after, cfg
    )
    return outcome(accepted, reason, bench.median_s, sloc_after, cx_after)
