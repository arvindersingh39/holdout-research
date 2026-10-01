"""Runs the SymPy check scripts in checks/ inside the sandbox (Phase 4)."""

from __future__ import annotations

from dataclasses import dataclass

from r2r.harness.sandbox import SandboxBackend
from r2r.harness.workspace import Workspace

OUTPUT_TAIL_CHARS = 1000


@dataclass(frozen=True)
class CheckResult:
    file: str
    passed: bool
    output_tail: str


def run_checks(ws: Workspace, sandbox: SandboxBackend, timeout_s: float) -> list[CheckResult]:
    results = []
    for path in sorted((ws.root / "checks").glob("check_*.py")):
        rel = path.relative_to(ws.root).as_posix()
        res = sandbox.run(["python", rel], timeout_s)
        output = (res.stdout + "\n" + res.stderr).strip()
        if res.timed_out:
            output = f"timed out after {timeout_s:.0f} s\n{output}"
        passed = res.exit_code == 0 and not res.timed_out
        results.append(CheckResult(rel, passed, output[-OUTPUT_TAIL_CHARS:]))
    return results
