"""Reference check (D54): the frozen tests must pass on the designer's own reference solution.

Red-before-green proves a test can fail; this proves it can pass. The reference lives outside
the workspace (runs/<id>/reference/<M>/), is never committed and never mounted for the coder.
The check runs on a temporary copy of the workspace with the reference overlaid on src/.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from r2r.contracts import FileSpec, TestResult
from r2r.harness.sandbox import SandboxBackend
from r2r.harness.tests import CaseOutcome, run_tests_detailed
from r2r.harness.workspace import RESULTS_DIR, Workspace

_SKIP = shutil.ignore_patterns(RESULTS_DIR, "__pycache__", ".pytest_cache")


class ReferenceError(ValueError):
    """A reference file outside src/ (it must mirror the stubs it replaces)."""


def store_reference(ref_dir: Path, files: list[FileSpec]) -> list[str]:
    for f in files:
        if PurePosixPath(f.path).parts[:1] != ("src",):
            raise ReferenceError(f"reference files must mirror src/: {f.path}")
    shutil.rmtree(ref_dir, ignore_errors=True)
    for f in files:
        target = ref_dir / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content)
    return [f.path for f in files]


def check_reference(
    ws: Workspace,
    ref_dir: Path,
    sandbox_for: Callable[[Workspace], SandboxBackend],
    *,
    run_id: str,
    milestone_id: str,
    timeout_s: float,
) -> tuple[TestResult, list[CaseOutcome]]:
    tmp = Path(tempfile.mkdtemp(prefix="ref-check-", dir=ws.root.parent))
    try:
        root = tmp / "workspace"
        shutil.copytree(ws.root, root, ignore=_SKIP)
        for src in ref_dir.rglob("*"):
            if src.is_file():
                dest = root / src.relative_to(ref_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    dest.chmod(0o644)
                dest.write_bytes(src.read_bytes())
        copy = Workspace(root, ws.git_dir, tmp / "frozen.json")
        return run_tests_detailed(
            copy,
            sandbox_for(copy),
            run_id=run_id,
            milestone_id=milestone_id,
            timeout_s=timeout_s,
            require_frozen=False,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # Docker may hold mounts a moment; best effort
