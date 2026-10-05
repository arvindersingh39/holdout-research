"""Harness without Docker: workspace layout, safe paths and git (the sandbox's isolation is
covered by `tests/docker/`)."""

from pathlib import Path

import pytest

from r2r.harness.gitops import Git
from r2r.harness.workspace import UnsafePath, Workspace, safe_path
from r2r.store.runs import run_paths


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    (ws.root / "src/slowpkg").mkdir(parents=True)
    (ws.root / "src/slowpkg/core.py").write_text("def slow_sum(n):\n    return sum(range(n))\n")
    return ws


def test_workspace_layout_and_external_git(tmp_path: Path) -> None:
    paths = run_paths("r", base=tmp_path, create=True)
    ws = Workspace.create(paths)
    for d in ("src", "bench", "figures", "tests/frozen", "checks"):
        assert (ws.root / d).is_dir()
    assert (ws.root / "pyproject.toml").is_file()
    assert not (ws.root / ".git").exists()  # git dir lives outside the workspace
    assert ws.git_dir == paths.root / "workspace.git"
    assert Workspace.create(paths).root == ws.root  # idempotent
    assert Git(ws).head()  # initial commit exists


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "../outside.py",
        "src/../../x.py",
        "tests/frozen/test_x.py",
        "checks/c.py",
        "pyproject.toml",
    ],
)
def test_safe_path_rejects(ws: Workspace, bad: str) -> None:
    with pytest.raises(UnsafePath):
        safe_path(ws.root, bad, allowed=("src", "bench"))


def test_safe_path_rejects_escaping_symlink(ws: Workspace, tmp_path: Path) -> None:
    (ws.root / "src" / "link").symlink_to(tmp_path)
    with pytest.raises(UnsafePath):
        safe_path(ws.root, "src/link/evil.py", allowed=("src",))


def test_safe_path_accepts(ws: Workspace) -> None:
    assert safe_path(ws.root, "src/slowpkg/core.py", ("src",)) == ws.root / "src/slowpkg/core.py"
    assert safe_path(ws.root, "bench/new.py", ("src", "bench")).name == "new.py"


def test_branches(ws: Workspace) -> None:
    git = Git(ws)
    base = git.commit("fixture")
    git.branch("trial-1", base)
    assert git.current_branch() == "trial-1"
    git.checkout("main")
    assert git.current_branch() == "main"


def test_planted_hook_never_runs(ws: Workspace) -> None:
    git = Git(ws)
    hook_dir = ws.root / ".git" / "hooks"  # a workspace .git would be attacker-controlled
    hook_dir.mkdir(parents=True)
    marker = ws.root / "pwned"
    (hook_dir / "pre-commit").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (hook_dir / "pre-commit").chmod(0o755)
    git.commit("with a planted hook")
    assert not marker.exists()


def test_commit_and_diff(ws: Workspace) -> None:
    git = Git(ws)
    base = git.commit("fixture")
    (ws.root / "src/slowpkg/core.py").write_text("def slow_sum(n):\n    return n * (n - 1) // 2\n")
    git.commit("closed form")
    assert "n * (n - 1)" in git.diff(base)
