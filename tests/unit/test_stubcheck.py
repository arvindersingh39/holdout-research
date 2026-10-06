"""D93: the coder's only finish line is the stub check: every stub the designer wrote is
implemented with the same parameters, the package imports, and a benchmark milestone has its
reproduce script. Plain Python plus one sandboxed import; no LLM."""

from pathlib import Path

import pytest

from r2r.harness.sandbox import LocalProcessBackend
from r2r.harness.stubcheck import check_code, record_stubs, reproduce_script
from r2r.harness.workspace import Workspace
from r2r.store.runs import run_paths

STUB = '''\
"""Squares."""


def square(x: float) -> float:
    """x squared."""
    raise NotImplementedError


class Grid:
    def __init__(self, n: int) -> None:
        raise NotImplementedError

    def total(self, scale: float = 1.0) -> float:
        ...
'''
IMPL = """\
def square(x: float) -> float:
    return x * x


class Grid:
    def __init__(self, n: int) -> None:
        self.n = n

    def total(self, scale: float = 1.0) -> float:
        return self.n * scale
"""


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    ws = Workspace.create(run_paths("r", base=tmp_path, create=True))
    (ws.root / "src/sq").mkdir(parents=True)
    (ws.root / "src/sq/__init__.py").write_text("")
    (ws.root / "src/sq/core.py").write_text(STUB)
    return ws


STUBS: list = []


@pytest.fixture(autouse=True)
def _record(ws: Workspace) -> None:
    """The stubs as the designer wrote them, recorded before the coder touches anything."""
    STUBS[:] = record_stubs(ws.root, ["src/sq/__init__.py", "src/sq/core.py"])


def _check(ws: Workspace, reproduce: str | None = None):
    stubs = STUBS
    return check_code(
        ws, LocalProcessBackend(ws), stubs, reproduce, timeout_s=60, run_id="r", milestone_id="M1"
    )


def test_records_functions_and_methods(ws: Workspace) -> None:
    stubs = record_stubs(ws.root, ["src/sq/core.py"])
    assert [(s["name"], s["params"]) for s in stubs] == [
        ("square", ["x"]),
        ("Grid.__init__", ["self", "n"]),
        ("Grid.total", ["self", "scale"]),
    ]
    assert {s["path"] for s in stubs} == {"src/sq/core.py"}


def test_untouched_stubs_fail(ws: Workspace) -> None:
    r = _check(ws)
    assert not r.passed and r.n_failed == 3
    assert all("not implemented" in f.message for f in r.failures)


def test_implemented_code_passes(ws: Workspace) -> None:
    (ws.root / "src/sq/core.py").write_text(IMPL)
    r = _check(ws)
    assert r.passed and r.n_failed == 0 and r.n_passed >= 4  # 3 stubs + the import


def test_changed_signature_and_missing_function_fail(ws: Workspace) -> None:
    (ws.root / "src/sq/core.py").write_text(
        IMPL.replace("def square(x: float)", "def square(y: float)").replace(
            "    def total(self, scale: float = 1.0) -> float:\n        return self.n * scale\n", ""
        )
    )
    msgs = " | ".join(f.message for f in _check(ws).failures)
    assert "square" in msgs and "parameters" in msgs and "Grid.total" in msgs and "missing" in msgs


def test_import_error_fails(ws: Workspace) -> None:
    (ws.root / "src/sq/core.py").write_text("import no_such_module\n" + IMPL)
    r = _check(ws)
    assert not r.passed
    assert any("import" in f.message and "no_such_module" in f.tail for f in r.failures)


def test_benchmark_milestone_needs_its_reproduce_script(ws: Workspace) -> None:
    (ws.root / "src/sq/core.py").write_text(IMPL)
    script = reproduce_script("M3")
    assert script == "bench/reproduce_m3.py"
    r = _check(ws, script)
    assert not r.passed and any(script in f.message for f in r.failures)
    (ws.root / script).parent.mkdir(exist_ok=True)
    (ws.root / script).write_text("def main(:\n")
    assert not _check(ws, script).passed  # must at least compile
    (ws.root / script).write_text("print('ok')\n")
    assert _check(ws, script).passed


def test_many_long_failures_are_shrunk_to_fit_the_cap(ws: Workspace) -> None:
    """TestResult has a token cap (budgets.yaml); ten import tracebacks must not overflow it."""
    for i in range(12):
        (ws.root / f"src/sq/m{i}.py").write_text(f"raise ImportError('{'x' * 1500}')\n")
    paths = [f"src/sq/m{i}.py" for i in range(12)]
    for p in paths:
        (ws.root / p).write_text(
            (ws.root / p).read_text() + "def f(a):\n    raise NotImplementedError\n"
        )
    stubs = record_stubs(ws.root, paths)
    for p in paths:  # implement, but every module still fails to import with a long message
        (ws.root / p).write_text(
            f"raise ImportError('{'x' * 1500}')\n\n\ndef f(a):\n    return a\n"
        )
    r = check_code(
        ws, LocalProcessBackend(ws), stubs, None, timeout_s=60, run_id="r", milestone_id="M1"
    )
    assert not r.passed and r.n_failed == 12 and r.failures  # built, within its cap


def test_script_stubs_must_be_implemented_but_are_not_imported(ws: Workspace) -> None:
    (ws.root / "scripts").mkdir(exist_ok=True)
    (ws.root / "scripts/report.py").write_text(
        "def main() -> None:\n    raise NotImplementedError\n"
    )
    (ws.root / "src/sq/core.py").write_text(IMPL)
    stubs = record_stubs(ws.root, ["src/sq/core.py", "scripts/report.py"])
    assert ("scripts/report.py", "main") in {(s["path"], s["name"]) for s in stubs}
    run = lambda: check_code(  # noqa: E731
        ws, LocalProcessBackend(ws), stubs, None, timeout_s=60, run_id="r", milestone_id="M1"
    )
    assert not run().passed
    (ws.root / "scripts/report.py").write_text("def main() -> None:\n    print('report')\n")
    r = run()
    assert r.passed and not any("scripts" in f.test_id for f in r.failures)
