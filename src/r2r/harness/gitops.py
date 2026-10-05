"""Workspace git commits, branches and diffs (Phase 4).

Every git call passes `--git-dir` (outside the workspace) and `--work-tree`, and disables hooks,
so nothing written by generated code inside the workspace can change what git runs.
"""

from __future__ import annotations

import subprocess

from r2r.harness.workspace import Workspace


class GitError(RuntimeError):
    pass


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
