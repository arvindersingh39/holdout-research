"""Owner reference data (D71): recorded values the tests must reproduce.

Small files in the proposal's `data/` folder are snapshotted into the run once (later edits to
the proposal folder don't change a running study), installed under `tests/frozen/data/` (frozen
and hash-verified with the tests, read-only for the coder), and shown verbatim to the designer so
tests load them instead of retyping numbers. Only plain-text formats are accepted: pickles and
other binaries can run code when loaded.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from r2r.harness.workspace import Workspace
from r2r.settings import ReferenceDataLimits

DATA_DIR = "tests/frozen/data"
ALLOWED = (".json", ".csv", ".tsv", ".txt", ".md")


class RefDataError(ValueError):
    pass


def snapshot(proposal_dir: Path, run_root: Path, lim: ReferenceDataLimits) -> list[str]:
    """Copy allowed files from <proposal>/data/ into <run>/data/ once; return their names."""
    dst = run_root / "data"
    if not dst.is_dir():
        src = proposal_dir / lim.dir
        files = sorted(p for p in src.glob("*") if p.is_file()) if src.is_dir() else []
        chosen = [p for p in files if p.suffix.lower() in ALLOWED]
        for p in chosen:
            if p.stat().st_size > lim.max_file_kb * 1024:
                raise RefDataError(
                    f"{p.name} is over {lim.max_file_kb} KB; trim it or raise the limit"
                )
        dst.mkdir(parents=True)
        for p in chosen:
            shutil.copy2(p, dst / p.name)
    return sorted(p.name for p in dst.glob("*") if p.is_file())


def install(ws: Workspace, run_root: Path) -> list[str]:
    """Put the snapshot under tests/frozen/data/ (missing files only); return workspace paths."""
    src = run_root / "data"
    if not src.is_dir():
        return []
    out = []
    for p in sorted(src.glob("*")):
        rel = f"{DATA_DIR}/{p.name}"
        target = ws.root / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        out.append(rel)
    return out


def describe(run_root: Path, max_chars: int) -> dict[str, str]:
    """Workspace path -> content for the agents, each capped at `max_chars`."""
    src = run_root / "data"
    if not src.is_dir():
        return {}
    shown = {}
    for p in sorted(src.glob("*")):
        text = p.read_text(errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n[... truncated: {len(text)} characters in total]"
        shown[f"{DATA_DIR}/{p.name}"] = text
    return shown
