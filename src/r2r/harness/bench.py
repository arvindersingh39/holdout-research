"""Repeated benchmarks with a coefficient-of-variation check (Phase 4).

Timing happens inside the container (D27): the runner imports the entrypoint, warms `main()`
up once, then times each call, so container start-up never counts toward the runtime.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass

from r2r.harness.sandbox import SandboxBackend
from r2r.harness.workspace import Workspace, safe_path

NOISY_CV = 0.10
NOISY_REPEATS = 9
_MARK = "R2R_BENCH "


class BenchFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class BenchResult:
    median_s: float
    cv: float
    repeats: int
    times: list[float]


def _once(
    ws: Workspace, sandbox: SandboxBackend, entrypoint: str, repeats: int, timeout_s: float
) -> BenchResult:
    res = sandbox.run(["python", "/harness/bench_runner.py", entrypoint, str(repeats)], timeout_s)
    if res.timed_out:
        raise BenchFailed(f"benchmark timed out after {timeout_s:.0f} s")
    lines = [ln for ln in res.stdout.splitlines() if ln.startswith(_MARK)]
    if res.exit_code != 0 or not lines:
        raise BenchFailed(f"benchmark failed (exit {res.exit_code}): {res.stderr[-500:]}")
    times = [float(t) for t in json.loads(lines[-1][len(_MARK) :])["times"]]
    mean = statistics.fmean(times)
    cv = statistics.stdev(times) / mean if len(times) > 1 and mean > 0 else 0.0
    return BenchResult(statistics.median(times), cv, len(times), times)


def run_bench(
    ws: Workspace, sandbox: SandboxBackend, entrypoint: str, repeats: int, timeout_s: float
) -> BenchResult:
    """Median wall time of `main()` in `entrypoint`; reruns with 9 repeats when CV > 10%."""
    safe_path(ws.root, entrypoint, allowed=("bench",))
    result = _once(ws, sandbox, entrypoint, repeats, timeout_s)
    if result.cv > NOISY_CV and repeats < NOISY_REPEATS:
        result = _once(ws, sandbox, entrypoint, NOISY_REPEATS, timeout_s)
    return result
