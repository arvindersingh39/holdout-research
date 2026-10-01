"""Contracts produced by the LLM-free harness and the #6 optimizer."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import Field, NonNegativeFloat, NonNegativeInt, PositiveInt

from r2r.contracts.base import Contract, Lines, Text
from r2r.contracts.common import Failure, Hotspot


class TestResult(Contract):
    __test__: ClassVar[bool] = False  # not a pytest test class
    milestone_scoped = True

    passed: bool
    n_passed: NonNegativeInt
    n_failed: NonNegativeInt
    failures: Annotated[list[Failure], Field(max_length=10)] = []
    frozen_ok: bool
    duration_s: NonNegativeFloat


class ProfileReport(Contract):
    milestone_scoped = True

    total_s: NonNegativeFloat
    hotspots: Annotated[list[Hotspot], Field(max_length=15)] = []


class OptimizationPatch(Contract):
    milestone_scoped = True

    diff: Lines(300, 30_000)  # unified diff
    technique: Literal["vectorize", "algorithmic", "numba", "caching", "io", "trim", "other"]
    rationale: Text(200, 1)


class OptimizationOutcome(Contract):
    milestone_scoped = True

    attempt: PositiveInt
    accepted: bool
    reason: Text(300, 1)
    runtime_before_s: NonNegativeFloat
    runtime_after_s: NonNegativeFloat | None = None
    sloc_before: NonNegativeInt
    sloc_after: NonNegativeInt | None = None
