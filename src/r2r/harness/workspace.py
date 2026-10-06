"""Creates and manages runs/<id>/workspace/: the git repo the agents build in (Phase 4).

The git directory lives outside the workspace (D26) so code running in the sandbox, which can
write to the workspace, can never plant hooks or rewrite history.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from r2r.store.runs import RunPaths

DIRS = ("src", "bench", "figures", "scripts", "tests/frozen", "checks")
FROZEN_DIRS = ("tests/frozen", "checks")
RESULTS_DIR = ".r2r"  # per-run harness output (JUnit XML), recreated for every run

PYPROJECT = """\
[project]
name = "workspace"
version = "0.0.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
addopts = "-p no:cacheprovider"
"""


class UnsafePath(ValueError):
    """A path outside the allowed workspace directories."""


@dataclass(frozen=True)
class Workspace:
    root: Path
    git_dir: Path
    frozen_manifest: Path

    @classmethod
    def create(cls, paths: RunPaths) -> Workspace:
        """Create the workspace and its git repo; safe to call again on an existing run."""
        ws = cls.open(paths)
        for d in DIRS:
            (ws.root / d).mkdir(parents=True, exist_ok=True)
        if not (ws.root / "pyproject.toml").exists():
            (ws.root / "pyproject.toml").write_text(PYPROJECT)
        if not ws.git_dir.exists():
            from r2r.harness.gitops import Git

            Git(ws).init()
        return ws

    @classmethod
    def open(cls, paths: RunPaths) -> Workspace:
        return cls(paths.workspace, paths.workspace_git, paths.frozen_manifest)


def safe_path(root: Path, rel: str, allowed: tuple[str, ...], allow_frozen: bool = False) -> Path:
    """Resolve `rel` inside `root`, only under one of the `allowed` dirs. Frozen dirs are refused
    unless `allow_frozen` (the math-designer flow, before `freeze()`)."""
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise UnsafePath(f"path must be relative, without '..': {rel!r}")
    resolved = (root / p).resolve()
    base = root.resolve()
    if not resolved.is_relative_to(base):
        raise UnsafePath(f"path escapes the workspace: {rel!r}")
    inner = resolved.relative_to(base)
    if not allow_frozen and any(inner.is_relative_to(f) for f in FROZEN_DIRS):
        raise UnsafePath(f"frozen directories are read-only: {rel!r}")
    if not any(inner.is_relative_to(a) for a in allowed):
        raise UnsafePath(f"path must be under {', '.join(allowed)}: {rel!r}")
    return root / inner
