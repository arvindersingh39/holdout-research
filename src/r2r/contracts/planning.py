"""Contracts from proposal to milestone spec: #1 retriever, #2/#3 planners, coordinator."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, PositiveInt, model_validator

from r2r.contracts.base import Contract, CriterionId, MilestoneId, Part, Text
from r2r.contracts.common import BlobRef, Criterion, Issue


class Proposal(Contract):
    title: Text(200, 1)
    text_ref: BlobRef
    paper_refs: list[BlobRef] = []


class Citation(Part):
    """BibTeX fields of one reference."""

    key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_:.-]{1,64}$")]
    entry_type: Literal["article", "book", "inproceedings", "misc", "techreport", "phdthesis"]
    title: Text(300, 1)
    authors: Annotated[list[Text(100, 1)], Field(max_length=30)] = []
    year: Annotated[int, Field(ge=1500, le=2100)] | None = None
    venue: Text(200) | None = None
    doi: Text(100) | None = None
    url: Text(300) | None = None


class PaperCard(Contract):
    citation: Citation
    problem: Text(600)
    method: Text(600)
    key_equations: Annotated[list[Text(300)], Field(max_length=3)] = []
    data: Text(400) = ""
    results: Text(600) = ""
    relevance: Text(400)


def _short_list(max_items: int, max_chars: int = 300):
    return Annotated[list[Text(max_chars, 1)], Field(max_length=max_items)]


BenchmarkRef = Annotated[str, Field(pattern=r"^[A-Za-z_][\w.\[\]-]{0,199}$")]


class Benchmark(Part):
    """D93: an experimental result stated in the proposal or a paper, to be reproduced. `verified`
    is set by code: the quote occurs in the source text and contains the value."""

    id: Annotated[str, Field(pattern=r"^BM\d{1,3}$")]
    quantity: Text(200, 1)
    value: float
    units: Text(60) = ""
    tolerance: Annotated[float, Field(ge=0)]  # half a unit of the last printed digit
    conditions: Text(300) = ""  # the setting: model, sizes, parameters, seeds
    source: Text(64, 1)  # "proposal" or a citation key
    quote: Text(300, 1)  # verbatim from the source
    verified: bool = False


class ResearchBrief(Contract):
    objective: Text(600, 1)
    hypotheses: _short_list(8) = []
    methods: _short_list(10) = []
    datasets: _short_list(10) = []
    constraints: _short_list(10) = []
    success_metrics: _short_list(10) = []
    open_questions: _short_list(10) = []
    benchmarks: Annotated[list[Benchmark], Field(max_length=30)] = []  # D93
    paper_cards: list[BlobRef] = []


class Milestone(Part):
    id: MilestoneId
    title: Text(200, 1)
    acceptance_criteria: Annotated[list[Criterion], Field(min_length=1, max_length=12)]
    depends_on: list[MilestoneId] = []
    # D93: refs into the reference data this milestone reproduces (`benchmarks.BM3`, or a path or
    # prefix in the owner's recorded values); the owner runs the check
    benchmarks: Annotated[list[BenchmarkRef], Field(max_length=20)] = []
    status: Literal["pending", "in_progress", "done", "failed", "skipped"] = "pending"


class Roadmap(Contract):
    version: PositiveInt
    goal: Text(500, 1)
    milestones: Annotated[list[Milestone], Field(min_length=1, max_length=20)]
    current_milestone_id: MilestoneId | None = None
    decisions_log: _short_list(10, 200) = []
    rationale: Text(800)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [m.id for m in self.milestones]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate milestone ids")
        if self.current_milestone_id is not None and self.current_milestone_id not in ids:
            raise ValueError(f"current_milestone_id {self.current_milestone_id} is not a milestone")
        for m in self.milestones:
            unknown = set(m.depends_on) - set(ids)
            if unknown:
                raise ValueError(f"{m.id} depends_on unknown milestones {sorted(unknown)}")
        return self


class MilestoneSpec(Contract):
    milestone_scoped = True

    tasks: _short_list(15, 800) = Field(min_length=1)
    interfaces: _short_list(15, 400) = []  # signatures with units and array shapes
    acceptance_criteria: Annotated[list[Criterion], Field(min_length=1, max_length=12)]
    context_refs: list[BlobRef] = []
    risks: _short_list(8) = []
    rationale: Text(600)


class CoordinatorVerdict(Contract):
    milestone_scoped = True

    verdict: Literal["pass", "revise"]
    issues: Annotated[list[Issue], Field(max_length=8)] = []

    @model_validator(mode="after")
    def _revise_has_issues(self) -> Self:
        if self.verdict == "revise" and not self.issues:
            raise ValueError("a revise verdict needs at least one issue")
        return self


class BaselineCard(Contract):
    """A published reference value for a test, quote-verified against its source (D53)."""

    milestone_scoped = True

    criterion_id: CriterionId
    claim: Text(300, 1)
    value: float | None = None
    units: Text(60) = ""
    precision: Text(100) = ""
    source_url: Annotated[str, Field(pattern=r"^https?://\S+$", max_length=500)]
    source_title: Text(200) = ""
    quote: Text(400, 1)
    license_note: Text(200) = ""
    verified: bool = False
