"""D88: contract checks. The designer describes interfaces, documented errors and output files as
data; plain harness code renders them into a frozen pytest file from a fixed template and runs
them like every check (red on stubs, green on the reference). General to any Python package."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from r2r.contracts import ContractCheck
from r2r.harness.contract_checks import CONTRACT_DIR, LIB_NAME, contract_file, write_contract_tests
from r2r.harness.redgreen import RIGHT_REASON
from r2r.harness.sandbox import LocalProcessBackend
from r2r.harness.tests import run_tests_detailed
from r2r.harness.workspace import Workspace
from r2r.store.runs import run_paths

IMPL = """
import json, pathlib
import numpy as np


class BadInput(ValueError):
    pass


def scale(x, factor: float = 2.0):
    if factor <= 0:
        raise BadInput("factor must be positive")
    return np.asarray(x) * factor


def report(out: str, n: int = 3):
    data = {"summary": {"n": n, "mean": 1.5}, "rows": list(range(n))}
    pathlib.Path(out).write_text(json.dumps(data))


class Counter:
    def __init__(self, start: int = 0):
        self.value = start

    def step(self, by: int):
        if by < 0:
            raise BadInput("by must be non-negative")
        self.value += by
        return self.value
"""
STUB = """
class BadInput(ValueError):
    pass


def scale(x, factor: float = 2.0):
    raise NotImplementedError


def report(out: str, n: int = 3):
    raise NotImplementedError


class Counter:
    def __init__(self, start: int = 0):
        raise NotImplementedError

    def step(self, by: int):
        raise NotImplementedError
"""


def c(**kw) -> ContractCheck:
    return ContractCheck.model_validate({"id": "C1", "criterion_ids": ["AC1"]} | kw)


CHECKS = [
    c(id="C1", kind="interface", call="pkg.core.scale", params=["x", "factor"]),
    c(id="C2", kind="interface", call="pkg.core.Counter.step", params=["by"]),
    c(
        id="C3",
        kind="raises",
        call="pkg.core.scale",
        args={"x": [1.0], "factor": 0},
        raises="pkg.core.BadInput",
    ),
    c(
        id="C4",
        kind="raises",
        call="pkg.core.scale",
        args={"x": [1.0], "factor": -1},
        raises="ValueError",
    ),  # a builtin base class of the documented error
    c(
        id="C5",
        kind="output",
        call="pkg.core.report",
        args={"out": "{tmp}/r.json", "n": 2},
        file="{tmp}/r.json",
        format="json",
        keys=["summary.n", "summary.mean", "rows"],
    ),
    c(
        id="C6",
        kind="raises",
        call="pkg.core.Counter",
        args={"start": 1},
        method="step",
        method_args={"by": -1},
        raises="pkg.core.BadInput",
    ),
]


# --- contract ------------------------------------------------------------------------------------


def test_kind_specific_fields_are_required() -> None:
    with pytest.raises(ValidationError, match="params"):
        c(kind="interface", call="pkg.core.scale")
    with pytest.raises(ValidationError, match="raises"):
        c(kind="raises", call="pkg.core.scale")
    with pytest.raises(ValidationError, match="file"):
        c(kind="output", call="pkg.core.report", format="json")
    with pytest.raises(ValidationError, match="tmp"):
        c(kind="output", call="pkg.core.report", file="/etc/r.json", format="json")
    with pytest.raises(ValidationError):
        c(kind="interface", call="not a dotted path", params=[])
    with pytest.raises(ValidationError):
        c(id="S1", kind="interface", call="pkg.core.scale", params=[])
    with pytest.raises(ValidationError):
        c(kind="output", call="pkg.core.report", file="{tmp}/r.x", format="docx")


# --- generated tests, run on stubs and on an implementation -------------------------------------


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    (ws.root / "src/pkg").mkdir(parents=True)
    (ws.root / "src/pkg/__init__.py").write_text("")
    return ws


def _run(ws: Workspace, code: str):
    (ws.root / "src/pkg/core.py").write_text(code)
    _, cases = run_tests_detailed(
        ws,
        LocalProcessBackend(ws),
        run_id="r",
        milestone_id="M1",
        timeout_s=60,
        require_frozen=False,
    )
    return {c.name: c for c in cases}


def test_rendered_file_names_tests_by_criterion_and_check(ws: Workspace) -> None:
    rel = write_contract_tests(ws.root, "M1", CHECKS)
    assert rel == contract_file("M1") == f"{CONTRACT_DIR}/test_contract_m1.py"
    assert (ws.root / CONTRACT_DIR / LIB_NAME).is_file()
    text = (ws.root / rel).read_text()
    assert "def test_AC1__C3_raises" in text and "def test_AC1__C5_output" in text


def test_stubs_fail_for_the_right_reason_except_interfaces(ws: Workspace) -> None:
    write_contract_tests(ws.root, "M1", CHECKS)
    cases = _run(ws, STUB)
    assert cases["test_AC1__C1_interface"].outcome == "passed"  # stubs carry the signature
    for name in ("C3_raises", "C4_raises", "C5_output", "C6_raises"):
        case = cases[f"test_AC1__{name}"]
        assert case.outcome == "failed" and RIGHT_REASON in case.text, name


def test_implementation_passes_every_check(ws: Workspace) -> None:
    write_contract_tests(ws.root, "M1", CHECKS)
    cases = _run(ws, IMPL)
    assert {n: c.outcome for n, c in cases.items()} == dict.fromkeys(cases, "passed")


@pytest.mark.parametrize(
    ("check", "broken", "why"),
    [
        (CHECKS[0], IMPL.replace("def scale(x, factor", "def scale(values, factor"), "params"),
        (CHECKS[2], IMPL.replace('raise BadInput("factor', 'raise TypeError("factor'), "TypeError"),
        (CHECKS[4], IMPL.replace('"mean": 1.5', '"avg": 1.5'), "summary.mean"),
        (
            CHECKS[4],
            IMPL.replace("pathlib.Path(out).write_text(json.dumps(data))", "pass"),
            "not written",
        ),
        (CHECKS[4], IMPL.replace("json.dumps(data)", "'not json'"), "json"),
        (
            c(
                id="C7",
                kind="output",
                call="pkg.core.report",
                args={"out": "{tmp}/r.json"},
                file="{tmp}/r.json",
                format="png",
            ),
            IMPL,
            "png",
        ),
    ],
    ids=["signature", "wrong-error", "missing-key", "no-file", "bad-format", "wrong-format"],
)
def test_broken_implementations_fail_with_a_reason(ws, check, broken, why) -> None:
    write_contract_tests(ws.root, "M1", [check])
    (case,) = _run(ws, broken).values()
    assert case.outcome == "failed" and why in case.text


def test_designer_may_not_write_the_generated_folder() -> None:
    from samples import math_design

    from r2r.contracts import MathDesign

    data = math_design().model_dump()
    data["files"].append(
        {"path": f"{CONTRACT_DIR}/test_contract_m1.py", "purpose": "test", "content": "x"}
    )
    with pytest.raises(ValidationError, match="generated by the harness"):
        MathDesign.model_validate(data)
    data = math_design().model_dump()
    data["spec"]["contract_checks"] = [CHECKS[0].model_dump(), CHECKS[0].model_dump()]
    with pytest.raises(ValidationError, match="unique"):
        MathDesign.model_validate(data)


def test_contract_schemas_are_provider_safe() -> None:
    """study-3 M9: Google's structured output rejected the MathDesign schema (INVALID_ARGUMENT)
    once fields used patternProperties (key patterns) and an empty any-JSON schema."""
    import json

    from pydantic import BaseModel

    import r2r.contracts as contracts

    for name in dir(contracts):
        obj = getattr(contracts, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            schema = obj.model_json_schema()
            assert "patternProperties" not in json.dumps(schema), name
            assert all(v != {} for v in schema.get("$defs", {}).values()), name


def test_args_are_json_text_validated_by_code() -> None:
    check = c(
        kind="raises", call="pkg.core.scale", args='{"x": [1.0], "factor": 0}', raises="ValueError"
    )
    assert check.args_dict() == {"x": [1.0], "factor": 0}
    assert c(
        kind="raises", call="pkg.core.scale", args={"x": 1}, raises="ValueError"
    ).args_dict() == {"x": 1}
    with pytest.raises(ValidationError, match="JSON object"):
        c(kind="raises", call="pkg.core.scale", args="[1, 2]", raises="ValueError")
    with pytest.raises(ValidationError, match="not an argument name"):
        c(kind="raises", call="pkg.core.scale", args='{"1bad": 1}', raises="ValueError")
