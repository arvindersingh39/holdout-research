"""#7 Writer (Phase 8): one call per paper section, each given only that section's inputs."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from r2r.contracts import PaperSection
from r2r.llm.gateway import CallContext, call_structured
from r2r.publish.latex import check_cites, cited_keys

ROLE = "writer"


class WriterInput(BaseModel):
    section: str
    title: str
    partial: bool
    data: str  # inside <document> tags
    cite_keys: dict[str, str]  # key -> what it refers to
    figures: list[str] = []  # figure labels in the paper
    note: str | None = None  # owner's revision request
    previous: str | None = None  # this section's LaTeX that failed to compile
    compile_error: str | None = None


def write_section(
    section: str,
    title: str,
    data: str,
    cite_keys: dict[str, str],
    ctx: CallContext,
    *,
    partial: bool,
    figures: Sequence[str] = (),
    note: str | None = None,
    previous: str | None = None,
    compile_error: str | None = None,
) -> PaperSection:
    payload = WriterInput(
        section=section,
        title=title,
        partial=partial,
        data=data,
        cite_keys=cite_keys,
        figures=list(figures),
        note=note,
        previous=previous,
        compile_error=compile_error,
    )

    def finalize(s: PaperSection) -> PaperSection:
        check_cites(s.latex, set(cite_keys))  # ValueError -> one repair with the message
        return s.model_copy(update={"name": section, "cites": sorted(cited_keys(s.latex))})

    return call_structured(ROLE, ROLE, payload, PaperSection, ctx, finalize=finalize)
