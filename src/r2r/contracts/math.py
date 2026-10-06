"""Contracts from #4 math designer: the spec the coder and writer share. SanityCheck and
ContractCheck (D83-D88) are no longer produced (D93); they stay so older runs still load."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal, Self

from pydantic import Field, PositiveFloat, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from r2r.contracts.base import Contract, CriterionId, Part, Text
from r2r.contracts.common import FileSpec, WorkspacePath

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


DottedPath = Annotated[str, Field(pattern=r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$", max_length=200)]
ArgName = Annotated[str, Field(pattern=r"^[A-Za-z_]\w*$", max_length=60)]
_ARG_NAME = re.compile(r"^[A-Za-z_]\w*$")


class ContractCheck(Part):
    """D88: an interface, documented error or output file, described as data. The harness renders
    it into a frozen pytest file from a fixed template; no hand-written test code."""

    id: Annotated[str, Field(pattern=r"^C\d{1,2}$")]
    criterion_ids: Annotated[list[CriterionId], Field(min_length=1, max_length=5)]
    kind: Literal["interface", "raises", "output"]
    call: DottedPath  # module-level callable or class, e.g. pkg.mod.func or pkg.mod.Cls.method
    # keyword arguments as a JSON object (text, so the schema stays a plain string for every
    # provider); {"np": [...]} is a numpy array and the string "{tmp}" a fresh temporary folder
    args: Text(2000) = "{}"
    method: ArgName | None = None  # then call this method on the returned object
    method_args: Text(2000) = "{}"
    params: Annotated[list[ArgName], Field(max_length=20)] | None = None  # interface, in order
    raises: DottedPath | ArgName | None = None  # exception class (builtin name or dotted path)
    file: Annotated[str, Field(max_length=200)] | None = None  # output path under "{tmp}"
    format: Literal["json", "csv", "npy", "png", "txt"] | None = None
    keys: Annotated[list[Text(120, 1)], Field(max_length=20)] = []  # json paths or csv columns
    max_seconds: Annotated[float, Field(gt=0, le=30)] = 10.0

    @field_validator("args", "method_args", mode="before")
    @classmethod
    def _json_object(cls, v: object) -> object:
        if isinstance(v, dict):
            v = json.dumps(v)
        if not isinstance(v, str):
            return v
        try:
            obj = json.loads(v)
        except ValueError:
            raise ValueError("args must be a JSON object") from None
        if not isinstance(obj, dict):
            raise ValueError("args must be a JSON object")
        bad = [k for k in obj if not _ARG_NAME.match(k)]
        if bad:
            raise ValueError(f"{bad} is not an argument name")
        return v

    def args_dict(self) -> dict[str, object]:
        return json.loads(self.args)

    def method_args_dict(self) -> dict[str, object]:
        return json.loads(self.method_args)

    @model_validator(mode="after")
    def _kind_fields(self) -> Self:
        if self.kind == "interface" and self.params is None:
            raise ValueError(f"{self.id}: an interface check lists its params in order")
        if self.kind == "raises" and not self.raises:
            raise ValueError(f"{self.id}: a raises check names the exception in `raises`")
        if self.kind == "output":
            if not (self.file and self.format):
                raise ValueError(f"{self.id}: an output check names its file and format")
            if not self.file.startswith("{tmp}/") or ".." in self.file:
                raise ValueError(f"{self.id}: output files are written under {{tmp}}/")
        return self


class MathSpec(Contract):
    """D93: the formulation the coder implements, with the stubs written as files. The check
    fields of D83-D90 are kept (hidden from the model's schema) so older runs still load."""

    milestone_scoped = True

    formulation: Text(4000, 1)  # LaTeX
    algorithm: Text(3000, 1)
    signatures: Annotated[list[Text(400, 1)], Field(min_length=1, max_length=20)]
    tolerances: dict[Text(120, 1), float] = {}
    bench: BenchSpec | None = None
    figure_scripts: Annotated[list[WorkspacePath], Field(max_length=10)] = []
    test_files: SkipJsonSchema[list[WorkspacePath]] = []
    check_files: SkipJsonSchema[list[WorkspacePath]] = []
    sanity_checks: SkipJsonSchema[list[SanityCheck]] = []
    contract_checks: SkipJsonSchema[list[ContractCheck]] = []
    unchecked_criteria: SkipJsonSchema[dict[str, str]] = {}


STUB_DIRS = ("src/", "bench/", "figures/", "scripts/")  # scripts/: command-line entry points (D97)


class MathDesign(Contract):
    """#4's call output only: the node writes `files` (stubs) and passes on `spec`."""

    milestone_scoped = True

    spec: MathSpec
    files: Annotated[list[FileSpec], Field(min_length=1, max_length=40)]

    @model_validator(mode="after")
    def _stubs_only(self) -> Self:
        bad = [f.path for f in self.files if not f.path.startswith(STUB_DIRS)]
        if bad:
            raise ValueError(
                "the design writes stubs only, under src/, bench/, figures/ or scripts/ (D93): "
                f"{bad}"
            )
        named = list(self.spec.figure_scripts)
        if self.spec.bench:
            named.append(self.spec.bench.entrypoint)
        written = {f.path for f in self.files}
        missing = [p for p in named if p not in written]
        if missing:
            raise ValueError(f"files missing for paths named in spec: {missing}")
        return self
