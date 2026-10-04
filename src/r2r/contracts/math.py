"""Contracts from #4 math designer: the spec the coder, harness, optimizer and writer share."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, PositiveFloat, model_validator

from r2r.contracts.base import Contract, CriterionId, Part, Text
from r2r.contracts.common import FileSpec, WorkspacePath

SANITY_DIR = "tests/frozen/sanity"
SanityKind = Literal[
    "initial_condition",  # the solution at t=0 or on the boundary equals the input
    "independent_solver",  # the same equations solved generically agree
    "limit_case",  # special or limiting inputs with known answers
    "conservation",  # sums, ranges, budgets, probabilities
    "convergence_order",  # refining the step changes the result as the method's order predicts
    "optimizer_claim",  # the optimum beats its starting points; the iterations ran
    "statistical",  # Monte Carlo within k standard errors of an independent value
]


class BenchSpec(Part):
    entrypoint: WorkspacePath
    problem_size: Text(200, 1)
    target_runtime_s: PositiveFloat | None = None


class SanityCheck(Part):
    """D83: a fast check of the package against an independent numerical oracle."""

    id: Annotated[str, Field(pattern=r"^S\d{1,2}$")]
    criterion_ids: Annotated[list[CriterionId], Field(min_length=1, max_length=5)]
    kind: SanityKind
    statement: Text(200, 1)
    file: WorkspacePath  # tests/frozen/sanity/test_sanity_*.py
    max_seconds: Annotated[float, Field(gt=0, le=30)] = 10.0


class MathSpec(Contract):
    milestone_scoped = True

    formulation: Text(4000, 1)  # LaTeX
    algorithm: Text(3000, 1)
    signatures: Annotated[list[Text(400, 1)], Field(min_length=1, max_length=20)]
    tolerances: dict[Text(120, 1), float] = {}  # names may be reference-data paths (D82)
    test_files: Annotated[list[WorkspacePath], Field(min_length=1, max_length=20)]
    check_files: Annotated[list[WorkspacePath], Field(max_length=20)] = []
    bench: BenchSpec | None = None
    figure_scripts: Annotated[list[WorkspacePath], Field(max_length=10)] = []
    sanity_checks: Annotated[list[SanityCheck], Field(max_length=12)] = []
    no_numerics_reason: Text(300) | None = None  # why a milestone has no sanity check


class MathDesign(Contract):
    """#4's call output only: the node writes `files` and passes on `spec`."""

    milestone_scoped = True

    spec: MathSpec
    files: Annotated[list[FileSpec], Field(max_length=40)]

    @model_validator(mode="after")
    def _files_cover_spec(self) -> Self:
        written = {f.path for f in self.files}
        named = [*self.spec.test_files, *self.spec.check_files, *self.spec.figure_scripts]
        if self.spec.bench:
            named.append(self.spec.bench.entrypoint)
        missing = [p for p in named if p not in written]
        if missing:
            raise ValueError(f"files missing for paths named in spec: {missing}")
        for c in self.spec.sanity_checks:
            name = c.file.rsplit("/", 1)[-1]
            if not (c.file.startswith(SANITY_DIR + "/") and name.startswith("test_sanity_")):
                raise ValueError(f"{c.id}: sanity files live in {SANITY_DIR}/test_sanity_*.py")
            if c.file not in self.spec.test_files:
                raise ValueError(f"{c.id}: {c.file} must also be listed in test_files")
        return self
