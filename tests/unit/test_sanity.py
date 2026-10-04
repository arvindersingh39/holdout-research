"""Phase 7e (D83): sanity checks compare outputs with an independent numerical oracle.

The oracle is written from the equations alone (numpy/scipy/math), never from the package, and
every check runs on a tiny instance within its time limit. Plain Python, no LLM.
"""

import pytest
from pydantic import ValidationError
from samples import math_design, math_spec

from r2r.contracts import MathDesign, SanityCheck
from r2r.harness.complexity import complexity
from r2r.harness.sanity import check_sanity, lint_sanity
from r2r.harness.tests import CaseOutcome

PKG = {"sis"}
GOOD = """
import numpy as np
from scipy.integrate import solve_ivp
from sis.dynamics import rollout


def oracle_logistic(i0, beta, t):
    sol = solve_ivp(lambda _, y: beta * y * (1 - y), (0, t), [i0], rtol=1e-10, atol=1e-12)
    return sol.y[0, -1]


def test_AC1__S1_matches_solver():
    assert abs(rollout(0.01, 0.3, 5.0) - oracle_logistic(0.01, 0.3, 5.0)) < 1e-6
"""


def _check(**kw) -> SanityCheck:
    base = {
        "id": "S1",
        "criterion_ids": ["AC1"],
        "kind": "independent_solver",
        "statement": "rollout agrees with solve_ivp on the logistic equation",
        "file": "tests/frozen/sanity/test_sanity_dynamics.py",
    }
    return SanityCheck.model_validate(base | kw)


# --- contract ------------------------------------------------------------------------------------


def test_sanity_check_contract() -> None:
    c = _check()
    assert c.max_seconds == 10.0
    with pytest.raises(ValidationError):
        _check(kind="vibes")
    with pytest.raises(ValidationError):
        _check(id="X1")
    with pytest.raises(ValidationError):
        _check(max_seconds=120)


def test_old_math_specs_still_load() -> None:
    """Checkpoints from earlier runs have no sanity fields."""
    assert math_spec().sanity_checks == [] and math_spec().no_numerics_reason is None


def test_design_must_write_sanity_files_under_the_sanity_dir() -> None:
    data = math_design().model_dump()
    path = "tests/frozen/sanity/test_sanity_bs.py"
    data["spec"]["sanity_checks"] = [_check(file=path).model_dump()]
    with pytest.raises(ValidationError, match="test_files"):
        MathDesign.model_validate(data)  # not listed in test_files
    data["spec"]["test_files"].append(path)
    data["files"].append({"path": path, "purpose": "test", "content": GOOD})
    MathDesign.model_validate(data)
    data["spec"]["sanity_checks"] = [_check(file="tests/frozen/test_bs.py").model_dump()]
    with pytest.raises(ValidationError, match="tests/frozen/sanity/"):
        MathDesign.model_validate(data)


# --- lint ----------------------------------------------------------------------------------------


def test_independent_oracle_passes_lint() -> None:
    assert lint_sanity(GOOD, PKG) == []


def test_oracle_calling_the_package_is_rejected() -> None:
    bad = GOOD.replace("sol = solve_ivp", "return rollout(i0, beta, t)\n    sol = solve_ivp")
    assert any("oracle_logistic" in p and "rollout" in p for p in lint_sanity(bad, PKG))


def test_oracle_importing_the_package_inside_is_rejected() -> None:
    bad = GOOD.replace("    sol = solve_ivp", "    import sis.dynamics\n    sol = solve_ivp")
    assert any("imports" in p for p in lint_sanity(bad, PKG))


def test_file_without_oracle_or_package_call_is_rejected() -> None:
    no_oracle = "from sis.dynamics import rollout\n\ndef test_AC1__x():\n    assert rollout(1)\n"
    assert any("oracle_" in p for p in lint_sanity(no_oracle, PKG))
    no_pkg = "import numpy as np\n\ndef oracle_a():\n    return 1\n\ndef test_AC1__x():\n"
    no_pkg += "    assert oracle_a() == 1\n"
    assert any("package" in p for p in lint_sanity(no_pkg, PKG))


def test_each_test_needs_an_oracle_and_the_package() -> None:
    lone = GOOD + "\n\ndef test_AC1__S2_only_package():\n    assert rollout(0.0, 0.3, 1.0) == 0\n"
    assert any("test_AC1__S2_only_package" in p for p in lint_sanity(lone, PKG))


def test_syntax_error_is_reported() -> None:
    assert any("syntax" in p.lower() for p in lint_sanity("def oops(:\n", PKG))


# --- check_sanity: required, lint and timing on the reference -----------------------------------


def _case(file: str, name: str, seconds: float) -> CaseOutcome:
    return CaseOutcome(f"{file}::{name}", file, name, "passed", "", seconds)


def _spec(checks, reason=None):
    return math_spec().model_copy(update={"sanity_checks": checks, "no_numerics_reason": reason})


def test_a_milestone_needs_a_sanity_check_or_a_reason(tmp_path) -> None:
    assert any("sanity check" in p for p in check_sanity(_spec([]), tmp_path, [], PKG))
    assert check_sanity(_spec([], reason="interfaces only"), tmp_path, [], PKG) == []
    assert check_sanity(_spec([]), tmp_path, [], PKG, required=False) == []


def test_slow_sanity_file_rejected_on_reference_timing(tmp_path) -> None:
    f = tmp_path / "tests/frozen/sanity/test_sanity_dynamics.py"
    f.parent.mkdir(parents=True)
    f.write_text(GOOD)
    spec = _spec([_check(max_seconds=2.0)])
    rel = "tests/frozen/sanity/test_sanity_dynamics.py"
    assert check_sanity(spec, tmp_path, [_case(rel, "test_AC1__S1_matches_solver", 0.4)], PKG) == []
    slow = check_sanity(spec, tmp_path, [_case(rel, "test_AC1__S1_matches_solver", 9.0)], PKG)
    assert any("9.0 s" in p and "2" in p for p in slow)


def test_missing_sanity_file_reported(tmp_path) -> None:
    assert any("missing" in p for p in check_sanity(_spec([_check()]), tmp_path, [], PKG))


# --- complexity metric for the simplifier ---------------------------------------------------------


def test_complexity_counts_statements_and_calls(tmp_path) -> None:
    long, short = tmp_path / "long", tmp_path / "short"
    for d, body in (
        (long, "def f(x):\n    y = g(x)\n    return h(y)\n"),
        (short, "def f(x):\n    return h(g(x))\n"),
    ):
        (d / "pkg").mkdir(parents=True)
        (d / "pkg" / "a.py").write_text(body)
    assert complexity(long) == 5  # def, assign, return + 2 calls
    assert complexity(short) == 4  # def, return + 2 calls
    assert complexity(tmp_path / "missing") == 0
