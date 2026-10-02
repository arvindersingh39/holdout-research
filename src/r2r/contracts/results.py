"""Contracts for milestone results, human gates and the final publication."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, NonNegativeFloat, NonNegativeInt, model_validator

from r2r.contracts.base import Contract, CriterionId, Lines, Part, Text
from r2r.contracts.common import BlobRef
from r2r.contracts.math import MathSpec
from r2r.contracts.planning import MilestoneSpec, Roadmap


class CriterionResult(Part):
    id: CriterionId
    passed: bool
    evidence: Text(120)


class ResultDigest(Contract):
    milestone_scoped = True

    status: Literal["met", "partial", "failed"]
    criteria: Annotated[list[CriterionResult], Field(max_length=12)]
    metrics: dict[Text(60, 1), float] = {}
    deviations: Text(500) = ""
    artifacts: Annotated[list[BlobRef], Field(max_length=10)] = []
    cost_usd: NonNegativeFloat
    tokens: NonNegativeInt


class HumanDecision(Contract):
    action: Literal["approve", "edit", "redirect", "stop"]
    note: Text(1000) = ""
    edited: Roadmap | MilestoneSpec | MathSpec | None = None

    @model_validator(mode="after")
    def _edit_has_replacement(self) -> Self:
        if self.action == "edit" and self.edited is None:
            raise ValueError("an edit decision needs the edited contract")
        return self


class PublicationBundle(Contract):
    mode: Literal["paper", "package"]
    artifacts: list[BlobRef]
    build_ok: bool
    log_tail: Lines(40, 4000) = ""


CiteKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_:.-]{1,64}$")]


class PaperSection(Contract):
    """One section of the paper, written by #7 from that section's inputs only (D65)."""

    name: Literal["abstract", "introduction", "related_work", "method", "experiments", "conclusion"]
    latex: Text(12000, 1)
    cites: Annotated[list[CiteKey], Field(max_length=40)] = []
