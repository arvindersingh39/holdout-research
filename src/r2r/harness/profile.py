"""cProfile runs condensed into a ProfileReport (Phase 4).

The profile is taken inside the sandbox; the host only parses the runner's JSON (never a pstats
file) and finds each function's line range with `ast`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from r2r.contracts import Hotspot, ProfileReport
from r2r.harness.sandbox import SandboxBackend
from r2r.harness.workspace import Workspace, safe_path

TOP = 5
_MARK = "R2R_PROFILE "


class ProfileFailed(RuntimeError):
    pass


def _locate(source: Path, line: int, name: str) -> tuple[str, int] | None:
    """(qualname, end line) of the function defined at `line` (decorators count)."""
    try:
        tree = ast.parse(source.read_text())
    except (OSError, SyntaxError):
        return None

    def walk(node: ast.AST, prefix: str) -> tuple[str, int] | None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                found = walk(child, f"{prefix}{child.name}.")
                if found:
                    return found
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                starts = {child.lineno, *(d.lineno for d in child.decorator_list)}
                if child.name == name and line in starts:
                    return f"{prefix}{child.name}", child.end_lineno or child.lineno
                found = walk(child, f"{prefix}{child.name}.")
                if found:
                    return found
        return None

    return walk(tree, "")


def run_profile(
    ws: Workspace,
    sandbox: SandboxBackend,
    entrypoint: str,
    timeout_s: float,
    *,
    run_id: str,
    milestone_id: str,
) -> ProfileReport:
    safe_path(ws.root, entrypoint, allowed=("bench",))
    res = sandbox.run(["python", "/harness/profile_runner.py", entrypoint], timeout_s)
    lines = [ln for ln in res.stdout.splitlines() if ln.startswith(_MARK)]
    if res.timed_out or res.exit_code != 0 or not lines:
        raise ProfileFailed(f"profiling failed (exit {res.exit_code}): {res.stderr[-500:]}")
    data = json.loads(lines[-1][len(_MARK) :])
    total = max(float(data["total_s"]), 1e-9)
    hotspots: list[Hotspot] = []
    for row in data["rows"]:
        file = str(row["file"])
        try:
            path = safe_path(ws.root, file, allowed=("src",))
        except ValueError:
            continue
        found = _locate(path, int(row["line"]), str(row["func"]))
        if found is None:
            continue
        qualname, end = found
        cum = float(row["cum_s"])
        hotspots.append(
            Hotspot(
                qualname=qualname,
                file=file,
                line_start=int(row["line"]),
                line_end=end,
                cum_s=cum,
                pct=min(100.0, 100 * cum / total),
            )
        )
        if len(hotspots) == TOP:
            break
    return ProfileReport(
        created_by="harness",
        run_id=run_id,
        milestone_id=milestone_id,
        total_s=total,
        hotspots=hotspots,
    )
