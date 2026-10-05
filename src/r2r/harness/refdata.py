"""Owner reference data (D71): recorded values a milestone's benchmarks must reproduce.

Small files in the proposal's `data/` folder are snapshotted into the run once (later edits to
the proposal folder don't change a running study). They stay in the run folder, never in the
workspace: the coder sees only refs (D93). Only plain-text formats are accepted: pickles and
other binaries can run code when loaded.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from r2r.settings import ReferenceDataLimits

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
