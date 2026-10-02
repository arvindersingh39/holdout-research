"""Reference check (D54): frozen tests must pass on a hidden reference solution before freezing.

Includes stand-ins for both pilot-1 test bugs: a shape mismatch (M1) and an impossible tolerance
when a Monte Carlo estimate has zero variance (M2).
"""

from pathlib import Path

import pytest

from r2r.contracts import FileSpec
from r2r.harness.redgreen import assess
from r2r.harness.reference import ReferenceError, check_reference, store_reference
from r2r.harness.sandbox import LocalProcessBackend
from r2r.harness.tests import run_tests_detailed
from r2r.harness.workspace import Workspace
from r2r.store.runs import run_paths

META = {"run_id": "run-test", "milestone_id": "M1"}
STUB = "import numpy as np\n\n\ndef f(x):\n    raise NotImplementedError\n"
REF = "import numpy as np\n\n\ndef f(x):\n    return np.asarray(x, dtype=float) * 2.0\n"
GOOD = """\
import numpy as np
from pkg.core import f


def test_AC1__doubles():
    np.testing.assert_allclose(f(np.ones((2, 3))), 2 * np.ones((2, 3)))
"""
SHAPE_BUG = """\
import numpy as np
from pkg.core import f


def test_AC1__doubles():
    rhs = 2 * np.ones((2, 1))  # same values, but assert_allclose does not broadcast
    np.testing.assert_allclose(f(np.ones((2, 3))), rhs)
"""
ZERO_VARIANCE_BUG = """\
from pkg.core import f


def test_AC1__tiny_price():
    est = float(f(0.0))  # no path in the money: the estimate is exactly 0
    assert abs(est - 1.07e-6) < 1e-12
"""


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return Workspace.create(run_paths("run-test", base=tmp_path, create=True))


def _setup(ws: Workspace, test_src: str) -> Path:
    (ws.root / "src/pkg").mkdir(parents=True)
    (ws.root / "src/pkg/__init__.py").write_text("")
    (ws.root / "src/pkg/core.py").write_text(STUB)
    (ws.root / "tests/frozen/test_m1.py").write_text(test_src)
    ref_dir = ws.root.parent / "reference" / "M1"
    store_reference(ref_dir, [FileSpec(path="src/pkg/core.py", purpose="reference", content=REF)])
    return ref_dir


def _check(ws: Workspace, ref_dir: Path):
    return check_reference(ws, ref_dir, LocalProcessBackend, timeout_s=60, **META)


def test_good_tests_pass_on_reference_and_fail_on_stubs(ws: Workspace) -> None:
    ref_dir = _setup(ws, GOOD)
    _, ref_cases = _check(ws, ref_dir)
    assert [c.outcome for c in ref_cases] == ["passed"]
    _, stub_cases = run_tests_detailed(
        ws, LocalProcessBackend(ws), timeout_s=60, require_frozen=False, **META
    )
    rg = assess(["AC1"], stub_cases, [], reference_cases=ref_cases)
    assert rg.ok and not rg.unsatisfiable
    assert "def f(x):\n    raise NotImplementedError" in (ws.root / "src/pkg/core.py").read_text()


@pytest.mark.parametrize("bug", [SHAPE_BUG, ZERO_VARIANCE_BUG], ids=["M1-shape", "M2-zero-var"])
def test_impossible_tests_are_caught_before_freezing(ws: Workspace, bug: str) -> None:
    ref_dir = _setup(ws, bug)
    _, stub_cases = run_tests_detailed(
        ws, LocalProcessBackend(ws), timeout_s=60, require_frozen=False, **META
    )
    rg_without = assess(["AC1"], stub_cases, [])
    assert rg_without.ok  # red-before-green alone is fooled, exactly as in pilot-1
    _, ref_cases = _check(ws, ref_dir)
    rg = assess(["AC1"], stub_cases, [], reference_cases=ref_cases)
    assert not rg.ok
    assert rg.unsatisfiable == [
        "tests.frozen.test_m1::test_AC1__" + bug.split("def test_AC1__")[1].split("(")[0]
    ]
    assert "can never pass" in rg.describe()


def test_missing_reference_is_not_ok(ws: Workspace) -> None:
    _setup(ws, GOOD)
    _, stub_cases = run_tests_detailed(
        ws, LocalProcessBackend(ws), timeout_s=60, require_frozen=False, **META
    )
    rg = assess(["AC1"], stub_cases, [], reference_cases=None)
    assert not rg.ok and rg.no_reference


def test_reference_never_enters_workspace_or_git(ws: Workspace) -> None:
    from r2r.harness.gitops import Git

    ref_dir = _setup(ws, GOOD)
    _check(ws, ref_dir)
    assert ref_dir.is_dir() and not ref_dir.is_relative_to(ws.root)
    Git(ws).commit("after check")
    tracked = Git(ws).run("ls-files")
    assert "reference" not in tracked
    assert (ws.root / "src/pkg/core.py").read_text() == STUB  # stubs untouched
    assert not any(p.name.startswith("ref-check") for p in ws.root.parent.iterdir())  # temp gone


def test_reference_paths_must_mirror_src(tmp_path: Path) -> None:
    with pytest.raises(ReferenceError):
        store_reference(
            tmp_path / "ref", [FileSpec(path="bench/x.py", purpose="reference", content="x")]
        )
