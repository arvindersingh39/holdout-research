"""Small values shared by several contracts."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, NonNegativeFloat, NonNegativeInt, PositiveInt

from r2r.contracts.base import CriterionId, Lines, Part, Text

BlobKind = Literal[
    "proposal", "paper", "paper_text", "chunk", "code", "log", "figure", "bib", "contract",
    "diff", "other",
]  # fmt: skip


def _workspace_path(v: str) -> str:
    p = PurePosixPath(v)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ValueError(f"path must be relative and stay inside the workspace: {v!r}")
    return v


WorkspacePath = Annotated[str, Field(max_length=200), AfterValidator(_workspace_path)]


class BlobRef(Part):
    """Pointer to heavy data in the blob store."""

    blob_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]  # sha256
    kind: BlobKind
    summary: Text(300)
    est_tokens: NonNegativeInt


class Criterion(Part):
    id: CriterionId
    text: Text(300, 1)


class Issue(Part):
    target: Literal["roadmap", "spec"]
    severity: Literal["low", "medium", "high"]
    text: Text(300, 1)


class Failure(Part):
    test_id: Text(200, 1)
    criterion_id: CriterionId | None = None
    message: Text(300)
    tail: Lines(15, 2000) = ""


class Hotspot(Part):
    qualname: Text(200, 1)
    file: WorkspacePath
    line_start: PositiveInt
    line_end: PositiveInt
    cum_s: NonNegativeFloat
    pct: Annotated[float, Field(ge=0, le=100)]


class FileSpec(Part):
    """A file the math designer writes into the workspace."""

    path: WorkspacePath
    purpose: Literal["test", "check", "stub", "bench", "figure_script", "fixture"]
    content: Text(40_000)
