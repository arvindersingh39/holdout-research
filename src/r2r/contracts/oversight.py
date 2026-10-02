"""Overseer output (Phase 7c, D63): briefings for other agents and flags for the owner.

The overseer advises only. Every item cites evidence ids (E1, E2, …) that code checks exist;
routing, approval and spending stay in plain Python.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from r2r.contracts.base import Contract, Part, Text

BriefedRole = Literal["long_planner", "short_planner", "math_designer", "coder", "optimizer"]
OversightPoint = Literal["plan", "spec", "design", "design_gate", "code", "result"]
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
