"""D95: after the owner approves a milestone, copy its committed code and a readable summary to
the run's export folder (outside the run, e.g. the owner's project folder). Plain code; no LLM.

The code comes from the workspace's main branch (`git archive`), so uncommitted scratch files of a
failed session never leak into the export. Re-approving a milestone replaces its folder.
"""

from __future__ import annotations

import re
import shutil
import tarfile
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from r2r.graph.render import render, render_review
from r2r.harness.gitops import Git
from r2r.harness.workspace import Workspace

CODE_DIRS = ("src", "bench", "figures", "results")
_SKIP = {"__pycache__", ".pytest_cache"}


def slug(milestone_id: str, title: str, max_len: int = 64) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{milestone_id}_{words}"[:max_len].rstrip("-")


def _copy_committed(ws: Workspace, dest: Path) -> None:
    git = Git(ws)
    present = [d for d in CODE_DIRS if git.run("ls-tree", "-d", "--name-only", "main", d).strip()]
    if not present:
        return
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "main.tar"
        git.run("archive", "--format=tar", "-o", str(archive), "main", *present)
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                parts = Path(member.name).parts
                if member.issym() or member.islnk() or ".." in parts or _SKIP & set(parts):
                    continue
                target = dest / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(tar.extractfile(member).read())


def _summary(state: Mapping[str, Any]) -> str:
    rm, digest = state["roadmap"], state["digest"]
    m = next(x for x in rm.milestones if x.id == digest.milestone_id)
    parts = [
        f"# {m.id} · {m.title}",
        "**Acceptance criteria**\n"
        + "\n".join(f"- {c.id}: {c.text}" for c in m.acceptance_criteria),
    ]
    if state.get("math_spec") is not None:
        parts.append(render(state["math_spec"]))
    parts.append(render(digest))
    review = render_review(
        "Design review",
        state.get("design_critique"),
        state.get("design_responses"),
        bool(state.get("design_revision_failed")),
    )
    if review:
        parts.append(review)
    ver = state.get("verification")
    if ver is not None:
        from r2r.graph.nodes import render_verification

        parts.append(render_verification(ver))
    parts.append(f"Cost ${digest.cost_usd:.2f} · {digest.tokens:,} tokens")
    return "\n\n".join(parts) + "\n"


def export_milestone(export_dir: Path, ws: Workspace, state: Mapping[str, Any]) -> Path:
    """Write <export_dir>/<M>_<slug>/ (code/ and summary.md) and <export_dir>/roadmap.md."""
    export_dir = Path(export_dir).expanduser().resolve()
    digest = state["digest"]
    m = next(x for x in state["roadmap"].milestones if x.id == digest.milestone_id)
    folder = export_dir / slug(m.id, m.title)
    if not folder.resolve().is_relative_to(export_dir):
        raise ValueError(f"export path escapes {export_dir}")
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "code").mkdir(parents=True)
    _copy_committed(ws, folder / "code")
    (folder / "summary.md").write_text(_summary(state))
    (export_dir / "roadmap.md").write_text(render(state["roadmap"]) + "\n")
    return folder
