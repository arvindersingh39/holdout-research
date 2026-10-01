"""Workspace git commits, diffs, apply and revert (Phase 4).

Every git call passes `--git-dir` (outside the workspace) and `--work-tree`, and disables hooks,
so nothing written by generated code inside the workspace can change what git runs.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from r2r.harness.workspace import FROZEN_DIRS, Workspace

PATCH_DIRS = ("src", "bench")
_DIFF_PATH = re.compile(r"^(?:\+\+\+ b/|--- a/)(\S+)", re.M)
_GIT_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.M)


class GitError(RuntimeError):
    pass


def patch_paths(diff: str) -> list[str]:
    paths = set(_DIFF_PATH.findall(diff))
    for a, b in _GIT_HEADER.findall(diff):
        paths |= {a, b}
    return sorted(p for p in paths if p != "/dev/null")


class Git:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = [
            "git",
            f"--git-dir={self.ws.git_dir}",
            f"--work-tree={self.ws.root}",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=r2r",
            "-c",
            "user.email=r2r@local",
            "-c",
            "commit.gpgsign=false",
            *args,
        ]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=self.ws.root)

    def run(self, *args: str) -> str:
        res = self._git(*args)
        if res.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {res.stderr.strip()[:500]}")
        return res.stdout

    def init(self) -> None:
        self.ws.git_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "-q", "-b", "main", "--bare", str(self.ws.git_dir)],
            check=True,
            capture_output=True,
        )
        self.run("config", "core.bare", "false")
        exclude = self.ws.git_dir / "info" / "exclude"
        exclude.parent.mkdir(exist_ok=True)
        exclude.write_text("__pycache__/\n*.pyc\n.r2r/\n.git/\n")
        self.commit("workspace created")

    def commit(self, message: str) -> str:
        self.run("add", "-A")
        self.run("commit", "-q", "--allow-empty", "-m", message)
        return self.head()

    def head(self) -> str:
        return self.run("rev-parse", "HEAD").strip()

    def current_branch(self) -> str:
        return self.run("rev-parse", "--abbrev-ref", "HEAD").strip()

    def diff(self, since: str) -> str:
        return self.run("diff", since, "--", ".")

    def branch(self, name: str, from_ref: str) -> None:
        self.run("checkout", "-q", "-B", name, from_ref)

    def checkout(self, name: str) -> None:
        self.run("checkout", "-q", name)

    def reset_hard(self, ref: str) -> None:
        self.run("reset", "-q", "--hard", ref)
        self.run("clean", "-q", "-fd", "--", "src", "bench")

    def apply_patch(
        self, diff: str, allowed_dirs: tuple[str, ...] = PATCH_DIRS
    ) -> tuple[bool, str]:
        """Apply a unified diff touching only `allowed_dirs` (default src/ and bench/).
        Returns (ok, reason)."""
        paths = patch_paths(diff)
        if not paths:
            return False, "no file paths found in the diff"
        allowed = tuple(f"{d}/" for d in allowed_dirs)
        frozen = tuple(f"{d}/" for d in FROZEN_DIRS)
        bad = [
            p
            for p in paths
            if not p.startswith(allowed) or p.startswith(frozen) or ".." in Path(p).parts
        ]
        if bad:
            return False, f"paths not allowed (only {', '.join(allowed_dirs)}/): {', '.join(bad)}"
        with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as f:
            f.write(diff if diff.endswith("\n") else diff + "\n")
            patch_file = f.name
        try:
            for args in (
                ("apply", "--check", "--recount", patch_file),
                ("apply", "--recount", patch_file),
            ):
                res = self._git(*args)
                if res.returncode != 0:
                    return False, f"patch does not apply: {res.stderr.strip()[:300]}"
            return True, "applied"
        finally:
            Path(patch_file).unlink(missing_ok=True)


def sloc(sandbox, timeout_s: float = 120) -> int:
    """Source lines of code under src/, counted by radon inside the sandbox (D28)."""
    res = sandbox.run(["radon", "raw", "-j", "src"], timeout_s)
    if res.exit_code != 0:
        raise GitError(f"radon failed: {res.stderr[-300:]}")
    data = json.loads(res.stdout)
    return sum(int(v.get("sloc", 0)) for v in data.values() if isinstance(v, dict))
