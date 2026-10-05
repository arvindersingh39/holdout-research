"""D93: the digest reports the code check and the benchmark comparison. With no tests, every
criterion is the owner's to judge; the status says whether the code is complete and whether the
benchmarks were reproduced."""

from pathlib import Path

import pytest
from samples import milestone_spec

from r2r.contracts import Criterion, Failure, Mismatch, TestResult, VerificationReport
from r2r.digest.builder import build_digest
from r2r.graph.render import render
from r2r.store.ledger import CallRecord, Ledger

META = {"run_id": "run-test", "milestone_id": "M1"}


def _spec():
    spec = milestone_spec()
    crits = [Criterion(id="AC1", text="a").model_dump(), Criterion(id="AC2", text="b").model_dump()]
    return spec.model_validate({**spec.model_dump(), "acceptance_criteria": crits})


def _check(failed: int = 0, passed: int = 3) -> TestResult:
    return TestResult(
        created_by="harness",
        **META,
        passed=failed == 0,
        n_passed=passed,
        n_failed=failed,
        failures=[Failure(test_id="src/x.py::f", message="f is not implemented yet")] * failed,
        frozen_ok=True,
        duration_s=0.1,
    )


def _ver(status: str) -> VerificationReport:
    mm = [Mismatch(ref="benchmarks.BM1", generated=0.5, recorded=0.4, tolerance=0.01)]
    return VerificationReport(
        created_by="harness",
        **META,
        status=status,
        n_checked=2,
        n_within=1 if status == "mismatch" else 2,
        n_mismatched=1 if status == "mismatch" else 0,
        mismatches=mm if status == "mismatch" else [],
    )


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    led = Ledger(tmp_path / "l.sqlite")
    led.record(
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
    return led


def test_complete_code_without_benchmarks_is_met_and_yours_to_judge(ledger: Ledger) -> None:
    d = build_digest(_spec(), _check(), None, ledger, **META)
    assert d.status == "met" and d.cost_usd == pytest.approx(0.25) and d.tokens == 150
    assert all(not c.checked and c.passed for c in d.criteria)
    assert "no benchmark" in d.criteria[0].evidence
    assert "AC1: not checked (you judge)" in render(d)
    assert d.metrics == {"check_passed": 3.0, "check_failed": 0.0}


def test_benchmarks_shape_the_status(ledger: Ledger) -> None:
    ok = build_digest(_spec(), _check(), _ver("verified"), ledger, **META)
    assert ok.status == "met" and "benchmarks verified" in ok.criteria[0].evidence
    assert ok.metrics["benchmarks_within"] == 2.0
    off = build_digest(_spec(), _check(), _ver("mismatch"), ledger, **META)
    assert off.status == "partial" and "mismatch" in off.deviations


def test_incomplete_code(ledger: Ledger) -> None:
    some = build_digest(_spec(), _check(failed=1, passed=2), None, ledger, **META)
    assert some.status == "partial" and not any(c.passed for c in some.criteria)
    none = build_digest(_spec(), _check(failed=2, passed=0), None, ledger, **META)
    assert none.status == "failed" and "code check" in none.deviations
