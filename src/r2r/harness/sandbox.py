"""Runs commands against a workspace: the Docker sandbox, plus an unisolated backend for tests.

Docker sandbox (production): network off; CPU, memory and PID limits from budgets.yaml; all
capabilities dropped; read-only root filesystem with a /tmp tmpfs; non-root user; no environment
variables from the host. The workspace is mounted read-write, with tests/frozen/ and checks/
re-mounted read-only over it, and the harness runners read-only at /harness.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from r2r.harness.workspace import FROZEN_DIRS, Workspace
from r2r.log import get_logger
from r2r.settings import SandboxLimits

log = get_logger("r2r.harness.sandbox")

SANDBOX_DIR = Path(__file__).resolve().parents[3] / "sandbox"
RUNNERS_DIR = Path(__file__).resolve().parent / "runners"
IMAGE_REPO = "r2r-sandbox"
OUTPUT_TAIL_CHARS = 100_000
TMPFS = {"/tmp": "rw,size=256m"}


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool


class SandboxBackend(Protocol):
    def run(self, cmd: list[str], timeout_s: float) -> RunResult: ...


def _tail(text: str) -> str:
    return text[-OUTPUT_TAIL_CHARS:]


def image_tag() -> str:
    """`r2r-sandbox:<hash of sandbox/>`, so any change to the image definition rebuilds it."""
    h = hashlib.sha256()
    for p in sorted(SANDBOX_DIR.iterdir()):
        if p.is_file():
            h.update(p.name.encode() + b"\0" + p.read_bytes())
    return f"{IMAGE_REPO}:{h.hexdigest()[:12]}"


def ensure_image(client: Any = None, build: bool = True) -> str:
    import docker

    client = client or docker.from_env()
    tag = image_tag()
    try:
        client.images.get(tag)
    except docker.errors.ImageNotFound:
        if not build:
            raise
        log.info("building sandbox image", extra={"tag": tag})
        client.images.build(path=str(SANDBOX_DIR), tag=tag, rm=True)
    return tag


class DockerBackend:
    def __init__(
        self,
        ws: Workspace,
        limits: SandboxLimits,
        image: str | None = None,
        client: Any = None,
    ) -> None:
        import docker

        self.ws = ws
        self.limits = limits
        self.client = client or docker.from_env()
        self.image = image or ensure_image(self.client)

    def _volumes(self) -> dict[str, dict[str, str]]:
        vols = {
            str(self.ws.root): {"bind": "/workspace", "mode": "rw"},
            str(RUNNERS_DIR): {"bind": "/harness", "mode": "ro"},
        }
        for d in FROZEN_DIRS:
            vols[str(self.ws.root / d)] = {"bind": f"/workspace/{d}", "mode": "ro"}
        return vols

    def run(self, cmd: list[str], timeout_s: float) -> RunResult:
        start = time.monotonic()
        container = self.client.containers.run(
            self.image,
            cmd,
            detach=True,
            network_mode="none",
            environment={},
            user="sandbox",
            working_dir="/workspace",
            nano_cpus=int(self.limits.cpus * 1e9),
            mem_limit=self.limits.memory,
            memswap_limit=self.limits.memory,
            pids_limit=self.limits.pids,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            read_only=True,
            tmpfs=TMPFS,
            volumes=self._volumes(),
        )
        timed_out = False
        try:
            try:
                exit_code = int(container.wait(timeout=timeout_s)["StatusCode"])
            except Exception:  # requests timeout or connection error while waiting
                timed_out = True
                exit_code = -9
                container.kill()
            out = container.logs(stdout=True, stderr=False).decode(errors="replace")
            err = container.logs(stdout=False, stderr=True).decode(errors="replace")
        finally:
            container.remove(force=True)
        return RunResult(exit_code, _tail(out), _tail(err), time.monotonic() - start, timed_out)


class LocalProcessBackend:
    """Runs harness commands as plain subprocesses in the workspace. NO ISOLATION: for the
    framework's own unit tests only (D29). Production code must use `DockerBackend`."""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def _translate(self, cmd: list[str]) -> list[str]:
        out = [sys.executable if c == "python" else c for c in cmd]
        return [
            str(RUNNERS_DIR / c.removeprefix("/harness/")) if c.startswith("/harness/") else c
            for c in out
        ]

    def run(self, cmd: list[str], timeout_s: float) -> RunResult:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": str(self.ws.root / ".r2r"),
        }
        start = time.monotonic()
        try:
            res = subprocess.run(
                self._translate(cmd),
                cwd=self.ws.root,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else ""
            return RunResult(-9, _tail(out), "", time.monotonic() - start, True)
        return RunResult(
            res.returncode, _tail(res.stdout), _tail(res.stderr), time.monotonic() - start, False
        )
