"""Contracts from #4 math designer: the spec the coder, harness, optimizer and writer share."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, PositiveFloat, model_validator

from r2r.contracts.base import Contract, Part, Text
from r2r.contracts.common import FileSpec, WorkspacePath


class BenchSpec(Part):
    entrypoint: WorkspacePath
    problem_size: Text(200, 1)
    target_runtime_s: PositiveFloat | None = None


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
        return self
