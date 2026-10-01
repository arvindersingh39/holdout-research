"""Run directories and metadata under runs/<run_id>/.

`runs` is a symlink to `runs.nosync/` so iCloud never syncs SQLite files mid-write (D12).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class RunPaths:
    root: Path

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def blobs(self) -> Path:
        return self.root / "blobs"

    @property
    def vectors(self) -> Path:
        return self.root / "vectors"

    @property
    def checkpoints(self) -> Path:
        return self.root / "checkpoints.sqlite"

    @property
    def ledger(self) -> Path:
        return self.root / "ledger.sqlite"


def ensure_runs_root(base: Path | None = None) -> Path:
    """Return `<base>/runs`, creating it as a symlink to `runs.nosync/` if it doesn't exist."""
    base = Path(base) if base is not None else Path.cwd()
    link = base / "runs"
    if not link.exists() and not link.is_symlink():
        (base / "runs.nosync").mkdir(exist_ok=True)
        link.symlink_to("runs.nosync", target_is_directory=True)
    return link


def run_paths(run_id: str, base: Path | None = None, create: bool = False) -> RunPaths:
    if not _RUN_ID.match(run_id) or run_id in {".", ".."}:
        raise ValueError(f"unsafe run id: {run_id!r}")
    root = ensure_runs_root(base) / run_id if create else (base or Path.cwd()) / "runs" / run_id
    paths = RunPaths(root)
    if create:
        for d in (paths.workspace, paths.blobs, paths.vectors):
            d.mkdir(parents=True, exist_ok=True)
    return paths
