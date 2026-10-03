"""#4 Math designer: MathSpec plus frozen tests, checks, stubs and bench (Phase 5).

Tests are written before any code exists. The design node writes the files through
`write_design`, proves red before green, and passes only the `MathSpec` downstream.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from r2r.contracts import BaselineCard, FileSpec, MathDesign, MathSpec, Milestone, MilestoneSpec
from r2r.harness.refdata import DATA_DIR
from r2r.harness.workspace import UnsafePath, Workspace, safe_path
from r2r.llm.gateway import CallContext, call_structured
from r2r.retrieval.context_pack import ContextPack

ROLE = "math_designer"
DESIGN_DIRS = ("tests/frozen", "checks", "src", "bench", "figures")


class DesignError(ValueError):
    """The design can't be written as given (path, overwrite or frozen-file conflict)."""


class DesignInput(BaseModel):
    spec: MilestoneSpec
    milestone: Milestone
    context: str  # retrieved chunks, each inside <document> tags
    existing_files: list[str]  # files already in the workspace (earlier milestones)
    feedback: str | None = None  # why the previous design was rejected
    baselines: list[BaselineCard] = []  # quote-verified published values (D52)
    baseline_note: str = ""
    data_files: dict[str, str] = {}  # owner's recorded values, path -> content (D71)
    proposal: str = ""  # the owner's full proposal when short, inside <document> tags


def run_math_designer(
    spec: MilestoneSpec,
    milestone: Milestone,
    pack: ContextPack,
    existing_files: list[str],
    feedback: str | None,
    ctx: CallContext,
    baselines: list[BaselineCard] | None = None,
    baseline_note: str = "",
    data_files: dict[str, str] | None = None,
    proposal: str = "",
) -> MathDesign:
    payload = DesignInput(
        spec=spec,
        milestone=milestone,
        context=pack.text,
        existing_files=existing_files,
        feedback=feedback,
        baselines=baselines or [],
        baseline_note=baseline_note,
        data_files=data_files or {},
        proposal=proposal,
    )
    return call_structured(ROLE, ROLE, payload, MathDesign, ctx)


def reference_files(design: MathDesign) -> list[FileSpec]:
    return [f for f in design.files if f.purpose == "reference"]


def _frozen_files(ws: Workspace) -> set[str]:
    if not ws.frozen_manifest.is_file():
        return set()
    return set(json.loads(ws.frozen_manifest.read_text())["files"])


def write_design(ws: Workspace, design: MathDesign, previous: MathSpec | None) -> list[str]:
    """Write the design's files. Never overwrites frozen files or code from earlier milestones;
    on a redesign, the previous attempt's files are removed first."""
    frozen = _frozen_files(ws)
    if previous is not None:
        old = [*previous.test_files, *previous.check_files, *previous.figure_scripts]
        if previous.bench:
            old.append(previous.bench.entrypoint)
        for rel in old:
            if rel not in frozen:
                (ws.root / rel).unlink(missing_ok=True)
    targets = []
    for f in design.files:
        if f.purpose == "reference":
            continue  # kept outside the workspace (D54)
        try:
            path = safe_path(ws.root, f.path, DESIGN_DIRS, allow_frozen=True)
        except UnsafePath as e:
            raise DesignError(str(e)) from None
        if f.path.startswith(DATA_DIR + "/"):
            raise DesignError(f"{f.path} is the owner's reference data (read-only); load it")
        if f.path in frozen:
            raise DesignError(f"{f.path} is a frozen file from an earlier milestone")
        if f.path.startswith("src/") and path.exists() and path.read_text() and previous is None:
            raise DesignError(f"{f.path} already exists: add new modules instead of rewriting it")
        targets.append((f, path))
    written = []
    for f, path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f.content)
        written.append(f.path)
    return written
