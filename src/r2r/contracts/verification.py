"""Phase 7d (D75): what the verifier measured. Written by code, never by an LLM."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from r2r.contracts.base import Contract, Lines, Part, Text

Scalar = float | int | bool | str | None


class Mismatch(Part):
    ref: Text(300, 1)
    generated: Scalar
    recorded: Scalar
    tolerance: float | None = None


class VerificationReport(Contract):
    """`status`: none (no verify scripts), verified (everything checked matches), mismatch,
    unchecked_only (values reported but none checkable), error (a script or report failed)."""

    milestone_scoped = True

    status: Literal["none", "verified", "mismatch", "unchecked_only", "error"]
    scripts: Annotated[list[Text(200)], Field(max_length=30)] = []
    failed_scripts: Annotated[list[Text(200)], Field(max_length=30)] = []
    n_checked: int = 0
    n_within: int = 0
    n_mismatched: int = 0
    n_unchecked: int = 0
    mismatches: Annotated[list[Mismatch], Field(max_length=20)] = []
    log_tail: Lines(40, 4000) = ""
