"""#4 Math designer: the formulation and the code stubs of one milestone (D93).

No tests, checks or reference solutions: the designer states the mathematics exactly and writes
stubs with the final signatures; the coder implements them and the stub check (plain Python)
confirms every stub is implemented. D94: the draft gets one overseer critique, and
`revise_design` answers it (every item changed or rebutted) before anything is written.
"""

from __future__ import annotations

from pydantic import BaseModel

from r2r.contracts import (
    Critique,
    CritiqueItem,
    CritiqueResponse,
    DesignRevision,
    MathDesign,
    MathSpec,
    Milestone,
    MilestoneSpec,
    unanswered,
)
from r2r.harness.gitops import Git
from r2r.harness.workspace import UnsafePath, Workspace, safe_path
from r2r.llm.gateway import CallContext, call_structured
from r2r.retrieval.context_pack import ContextPack

ROLE = "math_designer"
DESIGN_DIRS = ("src", "bench", "figures", "scripts")


class DesignError(ValueError):
    """The design can't be written as given (a path, or an overwrite of earlier code)."""


class DesignInput(BaseModel):
    spec: MilestoneSpec
    milestone: Milestone
    context: str  # retrieved chunks, each inside <document> tags
    existing_files: list[str]  # files already in the workspace (earlier milestones)
    feedback: str | None = None  # why the previous design was rejected, or coordinator notes
    benchmarks: list[dict[str, str]] = []  # refs to reproduce with descriptions; never values
    proposal: str = ""  # the owner's full proposal when short, inside <document> tags
    draft: MathDesign | None = None  # D94: your draft, to revise
    critique: list[CritiqueItem] = []  # D94: answer every item: changed or rebutted


def run_math_designer(
    spec: MilestoneSpec,
    milestone: Milestone,
    pack: ContextPack,
    existing_files: list[str],
    feedback: str | None,
    ctx: CallContext,
    benchmarks: list[dict[str, str]] | None = None,
    proposal: str = "",
) -> MathDesign:
    payload = DesignInput(
        spec=spec,
        milestone=milestone,
        context=pack.text,
        existing_files=existing_files,
        feedback=feedback,
        benchmarks=benchmarks or [],
        proposal=proposal,
    )
    return call_structured(ROLE, ROLE, payload, MathDesign, ctx)


def revise_design(
    spec: MilestoneSpec,
    milestone: Milestone,
    pack: ContextPack,
    existing_files: list[str],
    ctx: CallContext,
    draft: MathDesign,
    critique: Critique,
    benchmarks: list[dict[str, str]] | None = None,
    proposal: str = "",
) -> tuple[MathDesign, list[CritiqueResponse]]:
    """D94: the draft revised once against the overseer's critique; every item answered."""
    payload = DesignInput(
        spec=spec,
        milestone=milestone,
        context=pack.text,
        existing_files=existing_files,
        benchmarks=benchmarks or [],
        proposal=proposal,
        draft=draft,
        critique=critique.items,
    )
    ids = [i.id for i in critique.items]

    def answered(r: DesignRevision) -> DesignRevision:
        missing = unanswered(r.responses, ids)
        if missing:
            raise ValueError(
                f"answer every critique item (changed or rebutted); missing: {missing}"
            )
        return r

    out = call_structured(ROLE, ROLE, payload, DesignRevision, ctx, finalize=answered)
    return out.design, [r for r in out.responses if r.id in ids]


def _reset_code(ws: Workspace) -> None:
    """src/ and bench/ go back to the last commit: stubs of rejected designs are uncommitted and
    broke every later design's imports (study-3 M7). Committed code is the earlier milestones'."""
    if not ws.git_dir.exists():
        return
    git = Git(ws)
    git.run("clean", "-q", "-fd", "--", "src", "bench")
    for top in ("src", "bench"):
        if git.run("ls-files", "--", top).strip():
            git.run("checkout", "-q", "HEAD", "--", top)


def write_design(ws: Workspace, design: MathDesign, previous: MathSpec | None) -> list[str]:
    """Write the stubs. Never overwrites code from earlier milestones; uncommitted files of earlier
    attempts are removed first."""
    _reset_code(ws)
    if previous is not None:
        for rel in previous.figure_scripts:
            if not Git(ws).run("ls-files", "--", rel).strip():
                (ws.root / rel).unlink(missing_ok=True)
    targets = []
    for f in design.files:
        try:
            path = safe_path(ws.root, f.path, DESIGN_DIRS)
        except UnsafePath as e:
            raise DesignError(str(e)) from None
        if f.path.startswith("src/") and path.exists() and path.read_text():
            raise DesignError(f"{f.path} already exists: add new modules instead of rewriting it")
        targets.append((f, path))
    written = []
    for f, path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f.content)
        written.append(f.path)
    return written
