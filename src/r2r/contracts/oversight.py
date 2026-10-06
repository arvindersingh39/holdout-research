"""Overseer output (Phase 7c, D63): briefings for other agents and flags for the owner, and
(D94) one critique of each new roadmap and design that its author must answer.

The overseer never writes another agent's contract or routes. Every item cites evidence ids
(E1, E2, …) that code checks exist; routing, approval and spending stay in plain Python.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from r2r.contracts.base import Contract, Part, Text
from r2r.contracts.math import MathDesign
from r2r.contracts.planning import Roadmap

BriefedRole = Literal["long_planner", "short_planner", "math_designer", "coder", "optimizer"]
OversightPoint = Literal["plan", "spec", "design", "design_gate", "tests", "code", "result"]
EvidenceId = Annotated[str, Field(pattern=r"^E\d{1,3}$")]


class Briefing(Part):
    """Problem-specific advice added to `role`'s next prompts."""

    role: BriefedRole
    text: Text(3000, 1)
    evidence: Annotated[list[EvidenceId], Field(max_length=8)] = []


class OversightFlag(Part):
    """Something the owner should look at, shown at the next gate."""

    severity: Literal["info", "warn", "high"]
    text: Text(600, 1)
    evidence: Annotated[list[EvidenceId], Field(max_length=8)] = []


class OversightReport(Contract):
    point: OversightPoint
    briefings: Annotated[list[Briefing], Field(max_length=10)] = []
    flags: Annotated[list[OversightFlag], Field(max_length=10)] = []


# --- D94: one-round reflection ------------------------------------------------------------------

CritiqueId = Annotated[str, Field(pattern=r"^C\d{1,2}$")]


class CritiqueItem(Part):
    """One change the author must make or rebut."""

    id: CritiqueId
    severity: Literal["warn", "high"]
    text: Text(600, 1)
    evidence: Annotated[list[EvidenceId], Field(min_length=1, max_length=8)]


class Critique(Contract):
    point: Literal["plan", "design"]
    items: Annotated[list[CritiqueItem], Field(max_length=10)] = []

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [i.id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("critique item ids must be unique")
        return self

    def high(self) -> list[str]:
        return [i.id for i in self.items if i.severity == "high"]


class CritiqueResponse(Part):
    id: CritiqueId
    verdict: Literal["changed", "rebutted"]
    text: Text(400, 1)  # what changed, or why the item is wrong


class RoadmapRevision(Contract):
    """The long-term planner's answer to a critique: the revised roadmap and one response per
    item."""

    roadmap: Roadmap
    responses: Annotated[list[CritiqueResponse], Field(max_length=10)]


class DesignRevision(Contract):
    """The math designer's answer to a critique."""

    milestone_scoped = True

    design: MathDesign
    responses: Annotated[list[CritiqueResponse], Field(max_length=10)]


def unanswered(responses: list[CritiqueResponse], ids: list[str]) -> list[str]:
    answered = {r.id for r in responses}
    return [i for i in ids if i not in answered]
