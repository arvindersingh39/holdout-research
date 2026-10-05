"""Phase 7e (D83): sanity checks compare outputs with an independent numerical oracle.

The oracle is written from the equations alone (numpy/scipy/math), never from the package, and
every check runs on a tiny instance within its time limit. Plain Python, no LLM.
"""

import pytest
from pydantic import ValidationError
from samples import math_design, math_spec

from r2r.contracts import MathDesign, MathSpec, SanityCheck
from r2r.harness.complexity import complexity
from r2r.harness.sanity import check_sanity, lint_sanity
from r2r.harness.tests import CaseOutcome

PKG = {"model"}
GOOD = """
import numpy as np
from scipy.integrate import solve_ivp
from model.ode import solve


def oracle_logistic(i0, beta, t):
    sol = solve_ivp(lambda _, y: beta * y * (1 - y), (0, t), [i0], rtol=1e-10, atol=1e-12)
    return sol.y[0, -1]


def test_AC1__S1_matches_solver():
    assert abs(solve(0.01, 0.3, 5.0) - oracle_logistic(0.01, 0.3, 5.0)) < 1e-6
"""


def _check(**kw) -> SanityCheck:
    base = {
        "id": "S1",
        "criterion_ids": ["AC1"],
        "kind": "independent_solver",
        "statement": "solve agrees with solve_ivp on the logistic equation",
        "file": "tests/frozen/sanity/test_sanity_ode.py",
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
    """Checkpoints from earlier runs have unit tests and no sanity fields."""
    old = math_spec().model_dump() | {"test_files": ["tests/frozen/test_bs.py"]}
    old.pop("sanity_checks"), old.pop("unchecked_criteria")
    spec = MathSpec.model_validate(old)
    assert spec.sanity_checks == [] and spec.unchecked_criteria == {}


def _design_data() -> dict:
    data = math_design().model_dump()
    data["files"][0]["content"] = GOOD
    return data


def test_sample_design_is_valid() -> None:
    MathDesign.model_validate(_design_data())


def test_only_sanity_checks_are_tests() -> None:
    """D87: frozen unit tests are replaced by sanity checks."""
    data = _design_data()
    data["spec"]["test_files"].append("tests/frozen/test_bs.py")
    data["files"].append({"path": "tests/frozen/test_bs.py", "purpose": "test", "content": "x"})
    with pytest.raises(ValidationError, match="only sanity checks"):
        MathDesign.model_validate(data)


def test_every_sanity_file_has_a_check_and_every_check_a_listed_file() -> None:
    data = _design_data()
    extra = "tests/frozen/sanity/test_sanity_extra.py"
    data["spec"]["test_files"].append(extra)
    data["files"].append({"path": extra, "purpose": "test", "content": GOOD})
    with pytest.raises(ValidationError, match="no sanity check"):
        MathDesign.model_validate(data)
    data = _design_data()
    data["spec"]["sanity_checks"].append(_check(id="S2", file=extra))
    with pytest.raises(ValidationError, match="test_files"):
        MathDesign.model_validate(data)


@pytest.mark.parametrize("how", ["check_files", "purpose"])
def test_sympy_checks_are_no_longer_written(how: str) -> None:
    data = _design_data()
    path = "checks/check_bs.py"
    data["files"].append({"path": path, "purpose": "check", "content": "assert True\n"})
    if how == "check_files":
        data["spec"]["check_files"] = [path]
    with pytest.raises(ValidationError, match="SymPy checks are no longer used"):
        MathDesign.model_validate(data)


# --- lint ----------------------------------------------------------------------------------------


def test_independent_oracle_passes_lint() -> None:
    assert lint_sanity(GOOD, PKG) == []


def test_oracle_calling_the_package_is_rejected() -> None:
    bad = GOOD.replace("sol = solve_ivp", "return solve(i0, beta, t)\n    sol = solve_ivp")
    assert any("oracle_logistic" in p and "solve" in p for p in lint_sanity(bad, PKG))


def test_oracle_importing_the_package_inside_is_rejected() -> None:
    bad = GOOD.replace("    sol = solve_ivp", "    import model.ode\n    sol = solve_ivp")
    assert any("imports" in p for p in lint_sanity(bad, PKG))


def test_file_without_oracle_or_package_call_is_rejected() -> None:
    no_oracle = "from model.ode import solve\n\ndef test_AC1__x():\n    assert solve(1)\n"
    assert any("oracle_" in p for p in lint_sanity(no_oracle, PKG))
    no_pkg = "import numpy as np\n\ndef oracle_a():\n    return 1\n\ndef test_AC1__x():\n"
    no_pkg += "    assert oracle_a() == 1\n"
    assert any("package" in p for p in lint_sanity(no_pkg, PKG))


def test_each_test_needs_an_oracle_and_the_package() -> None:
    lone = GOOD + "\n\ndef test_AC1__S2_only_package():\n    assert solve(0.0, 0.3, 1.0) == 0\n"
    assert any("test_AC1__S2_only_package" in p for p in lint_sanity(lone, PKG))


def test_syntax_error_is_reported() -> None:
    assert any("syntax" in p.lower() for p in lint_sanity("def oops(:\n", PKG))


# --- check_sanity: required, lint and timing on the reference -----------------------------------


def _case(file: str, name: str, seconds: float) -> CaseOutcome:
    return CaseOutcome(f"{file}::{name}", file, name, "passed", "", seconds)


def _spec(checks, unchecked=None):
    return math_spec().model_copy(
        update={"sanity_checks": checks, "unchecked_criteria": unchecked or {}}
    )


def test_criteria_without_a_check_are_skipped_not_rejected(tmp_path) -> None:
    """D90: a criterion with no sanity or contract check no longer rejects the design; it is
    listed as unchecked (the owner judges it). Unknown criterion ids are still a problem."""
    from r2r.harness.sanity import skip_unchecked

    f = tmp_path / "tests/frozen/sanity/test_sanity_ode.py"
    f.parent.mkdir(parents=True)
    f.write_text(GOOD)
    spec = _spec([_check()], {"AC3": "writes a JSON file"})
    assert check_sanity(spec, tmp_path, [], PKG, criteria=["AC1", "AC2", "AC3"]) == []
    out = skip_unchecked(spec, ["AC1", "AC2", "AC3"])
    assert out.unchecked_criteria["AC3"] == "writes a JSON file"  # the designer's reason stays
    assert "no sanity or contract check" in out.unchecked_criteria["AC2"]
    assert "AC1" not in out.unchecked_criteria
    nothing = skip_unchecked(_spec([]), ["AC1"])
    assert list(nothing.unchecked_criteria) == ["AC1"]
    unknown = _spec([_check(criterion_ids=["AC9"])])
    assert any("AC9" in p for p in check_sanity(unknown, tmp_path, [], PKG, criteria=["AC1"]))


def test_slow_sanity_file_rejected_on_reference_timing(tmp_path) -> None:
    f = tmp_path / "tests/frozen/sanity/test_sanity_ode.py"
    f.parent.mkdir(parents=True)
    f.write_text(GOOD)
    spec = _spec([_check(max_seconds=2.0)])
    rel = "tests/frozen/sanity/test_sanity_ode.py"
    fast = [_case(rel, "test_AC1__S1_matches_solver", 0.4)]
    assert check_sanity(spec, tmp_path, fast, PKG, criteria=["AC1"]) == []
    slow_case = [_case(rel, "test_AC1__S1_matches_solver", 9.0)]
    slow = check_sanity(spec, tmp_path, slow_case, PKG, criteria=["AC1"])
    assert any("9.0 s" in p and "2" in p for p in slow)


def test_missing_sanity_file_reported(tmp_path) -> None:
    assert any(
        "missing" in p for p in check_sanity(_spec([_check()]), tmp_path, [], PKG, criteria=["AC1"])
    )


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


# --- D92: earlier, already-verified modules may serve as oracles ---------------------------------

WRAPPER = """
import numpy as np
from model.ode import solve
from model.batch import solve_many


def oracle_each(inits):
    return np.array([solve(x, 0.3, 1.0) for x in inits])


def test_AC1__S1_batch_matches_scalar():
    inits = [0.01, 0.02]
    np.testing.assert_allclose(solve_many(inits, 0.3, 1.0), oracle_each(inits))
"""


def test_trusted_module_may_be_the_oracle() -> None:
    assert lint_sanity(WRAPPER, PKG, trusted={"model.ode"}) == []
    untrusted = lint_sanity(WRAPPER, PKG)  # without the rule the oracle uses the package
    assert any("oracle_each uses solve" in p for p in untrusted)


def test_the_new_code_is_never_trusted() -> None:
    cheat = WRAPPER.replace("solve(x, 0.3, 1.0) for x in inits", "solve_many(inits, 0.3, 1.0)")
    cheat = cheat.replace("np.array([solve_many(inits, 0.3, 1.0)])", "solve_many(inits, 0.3, 1.0)")
    problems = lint_sanity(cheat, PKG, trusted={"model.ode"})
    assert any("solve_many" in p for p in problems)


def test_a_check_must_call_the_new_code() -> None:
    only_old = WRAPPER.replace("solve_many(inits, 0.3, 1.0)", "solve(0.01, 0.3, 1.0)")
    problems = lint_sanity(only_old, PKG, trusted={"model.ode"})
    assert any("test_AC1__S1_batch_matches_scalar" in p for p in problems)


def test_trusted_modules_are_committed_ones_the_design_does_not_touch(tmp_path) -> None:
    from r2r.harness.gitops import Git
    from r2r.harness.sanity import trusted_modules
    from r2r.harness.workspace import Workspace
    from r2r.store.runs import run_paths

    ws = Workspace.create(run_paths("r", base=tmp_path, create=True))
    for rel in ("src/model/__init__.py", "src/model/ode.py", "src/model/env.py"):
        (ws.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws.root / rel).write_text("x = 1\n")
    Git(ws).commit("earlier milestones")
    (ws.root / "src/model/batch.py").write_text("y = 2\n")  # this design's new module
    trusted = trusted_modules(ws, written=["src/model/batch.py", "src/model/env.py"])
    assert trusted == {"model.ode"}  # env.py is touched by the design, batch.py is new
