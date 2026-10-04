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
    "conservation",  # preserved totals, domains and constraints of the outputs
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
    test_files: Annotated[list[WorkspacePath], Field(max_length=20)]  # sanity files (D87)
    check_files: Annotated[list[WorkspacePath], Field(max_length=20)] = []  # pre-D87 runs only
    bench: BenchSpec | None = None
    figure_scripts: Annotated[list[WorkspacePath], Field(max_length=10)] = []
    sanity_checks: Annotated[list[SanityCheck], Field(max_length=12)] = []
    # D87: criteria with no numerical output, and why; the owner judges them at the result gate
    unchecked_criteria: dict[CriterionId, Text(200, 1)] = {}


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
        if self.spec.check_files or any(f.purpose == "check" for f in self.files):
            raise ValueError(
                "SymPy checks are no longer used (D87): check the maths numerically "
                "in sanity checks"
            )
        unit = [p for p in self.spec.test_files if not _is_sanity_file(p)]
        if unit:
            raise ValueError(
                f"only sanity checks are tests (D87), in {SANITY_DIR}/test_sanity_*.py; not: {unit}"
            )
        for c in self.spec.sanity_checks:
            if not _is_sanity_file(c.file):
                raise ValueError(f"{c.id}: sanity files live in {SANITY_DIR}/test_sanity_*.py")
            if c.file not in self.spec.test_files:
                raise ValueError(f"{c.id}: {c.file} must also be listed in test_files")
        used = {c.file for c in self.spec.sanity_checks}
        unused = [p for p in self.spec.test_files if p not in used]
        if unused:
            raise ValueError(f"no sanity check names these files: {unused}")
        return self


def _is_sanity_file(path: str) -> bool:
    return path.startswith(SANITY_DIR + "/") and path.rsplit("/", 1)[-1].startswith("test_sanity_")
