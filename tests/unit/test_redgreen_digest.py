"""Red-before-green assessment (D32), per-test outcomes, and the minimal digest builder (D33)."""

import shutil
from pathlib import Path

import pytest

from r2r.contracts import Criterion
from r2r.digest.builder import build_digest
from r2r.harness.checks import CheckResult
from r2r.harness.redgreen import assess
from r2r.harness.sandbox import LocalProcessBackend
from r2r.harness.tests import CaseOutcome, run_tests_detailed
from r2r.harness.workspace import Workspace, safe_path
from r2r.store.ledger import CallRecord, Ledger
from r2r.store.runs import run_paths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "slow_project"
META = {"run_id": "run-test", "milestone_id": "M1"}
NIE = "NotImplementedError: stub"


def case(name: str, outcome: str, text: str = "", file: str = "tests/frozen/test_m1.py"):
    return CaseOutcome(test_id=f"{file}::{name}", file=file, name=name, outcome=outcome, text=text)


def test_assess_ok() -> None:
    cases = [
        case("test_AC1__a", "failed", NIE),
        case("test_AC2__b[1]", "error", NIE),
        case("test_AC2__c", "passed"),
    ]
    rg = assess(["AC1", "AC2"], cases, [CheckResult("checks/c.py", True, "")])
    assert rg.ok and not rg.vacuous and not rg.broken and not rg.failing_checks


def test_assess_vacuous_and_broken() -> None:
    cases = [
        case("test_AC1__passes_on_stub", "passed"),  # AC1: nothing red → vacuous
        case("test_AC2__wrong_reason", "failed", "ImportError: no module named pkgx"),  # broken
        case("test_AC2__right_reason", "failed", NIE),
    ]
    rg = assess(["AC1", "AC2", "AC3"], cases, [CheckResult("checks/c.py", False, "assert")])
    assert not rg.ok
    assert rg.vacuous == ["AC1", "AC3"]  # AC3 has no test at all
    assert rg.broken == ["tests/frozen/test_m1.py::test_AC2__wrong_reason"]
    assert rg.failing_checks == ["checks/c.py"]
    text = rg.describe()
    assert "AC1" in text and "AC3" in text and "ImportError" in text and "checks/c.py" in text


def test_assess_only_counts_this_milestones_files() -> None:
    old = case("test_AC1__from_m1", "passed", file="tests/frozen/test_m1.py")
    new = case("test_AC1__new", "failed", NIE, file="tests/frozen/test_m2.py")
    rg = assess(["AC1"], [old, new], [], test_files=["tests/frozen/test_m2.py"])
    assert rg.ok
    rg = assess(["AC1"], [old], [], test_files=["tests/frozen/test_m2.py"])
    assert rg.vacuous == ["AC1"]


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    for sub in ("src", "bench", "tests/frozen", "checks"):
        shutil.copytree(FIXTURE / sub, ws.root / sub, dirs_exist_ok=True)
    return ws


def test_run_tests_detailed_lists_every_case(ws: Workspace) -> None:
    result, cases = run_tests_detailed(
        ws, LocalProcessBackend(ws), timeout_s=60, require_frozen=False, **META
    )
    assert len(cases) == result.n_passed + result.n_failed == 7
    by_name = {c.name: c for c in cases}
    assert by_name["test_AC2__deliberately_wrong"].outcome == "failed"
    assert by_name["test_AC1__compute_is_zero"].outcome == "passed"
    assert all(c.file == "tests/frozen/test_core.py" for c in cases)


def test_safe_path_allow_frozen(ws: Workspace) -> None:
    allowed = ("tests/frozen", "checks", "src")
    p = safe_path(ws.root, "tests/frozen/test_m1.py", allowed, allow_frozen=True)
    assert p == ws.root / "tests/frozen/test_m1.py"


def test_build_digest(tmp_path: Path) -> None:
    from samples import milestone_spec

    spec = milestone_spec()  # AC1 only
    spec = spec.model_validate(
        {
            **spec.model_dump(),
            "acceptance_criteria": [
                Criterion(id="AC1", text="a").model_dump(),
                Criterion(id="AC2", text="b").model_dump(),
            ],
        }
    )
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record(
        CallRecord(
            run_id="run-test",
            milestone_id="M1",
            role="coder",
            model="x/y",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.25,
        )
    )
    cases = [
        case("test_AC1__a", "passed"),
        case("test_AC1__b", "passed"),
        case("test_AC2__c", "failed", "assert 1 == 2"),
    ]
    d = build_digest(spec, cases, ledger, test_files=["tests/frozen/test_m1.py"], **META)
    assert d.status == "partial"
    crit = {c.id: c for c in d.criteria}
    assert crit["AC1"].passed and crit["AC1"].evidence == "2/2 tests pass"
    assert not crit["AC2"].passed and "0/1" in crit["AC2"].evidence
    assert d.metrics == {"tests_passed": 2.0, "tests_failed": 1.0}
    assert d.cost_usd == pytest.approx(0.25) and d.tokens == 150
    assert "AC2" in d.deviations

    all_ok = [case("test_AC1__a", "passed"), case("test_AC2__c", "passed")]
    assert build_digest(spec, all_ok, ledger, test_files=None, **META).status == "met"
    none = [case("test_AC1__a", "failed", "x"), case("test_AC2__c", "failed", "x")]
    assert build_digest(spec, none, ledger, test_files=None, **META).status == "failed"
