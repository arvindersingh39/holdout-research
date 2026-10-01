"""Runs frozen tests and builds a TestResult (Phase 4).

Test names follow `test_<CRITERION>__<what>` (e.g. `test_AC2__put_call_parity`), so each
failure maps to an acceptance criterion without pytest plugins.
"""

from __future__ import annotations

import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Literal

from r2r.contracts import Failure, TestResult
from r2r.harness.frozen import verify
from r2r.harness.sandbox import SandboxBackend
from r2r.harness.workspace import RESULTS_DIR, Workspace

MAX_FAILURES = 10
MESSAGE_CHARS = 300
TAIL_LINES = 15
TAIL_CHARS = 2000
MAX_REPORT_BYTES = 2_000_000
_CRITERION = re.compile(r"^test_(AC\d{1,3})__")


@dataclass(frozen=True)
class CaseOutcome:
    """One test case from the JUnit report (harness-internal, not a contract)."""

    test_id: str
    file: str  # e.g. tests/frozen/test_m1_pricer.py
    name: str  # e.g. test_AC2__parity[0.5]
    outcome: Literal["passed", "failed", "error", "skipped"]
    text: str  # failure message plus traceback tail, empty when passed

    @property
    def criterion_id(self) -> str | None:
        m = _CRITERION.match(self.name)
        return m.group(1) if m else None


class UnsafeReport(ValueError):
    pass


def _file_of(classname: str, name: str) -> str:
    dotted = classname or name  # collection errors put the module path in `name`
    parts = dotted.split(".")
    while parts and not parts[-1].startswith("test"):
        parts.pop()  # drop class names such as TestPricer
    return "/".join(parts) + ".py" if parts else dotted


def parse_cases(xml_text: str) -> list[CaseOutcome]:
    if len(xml_text) > MAX_REPORT_BYTES or "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise UnsafeReport("unsafe or oversized JUnit report rejected")
    cases = []
    for case in ET.fromstring(xml_text).iter("testcase"):
        classname, name = case.get("classname", ""), case.get("name", "")
        test_id = "::".join(x for x in (classname, name) if x)
        outcome: Literal["passed", "failed", "error", "skipped"] = "passed"
        text = ""
        for tag in ("failure", "error", "skipped"):
            el = case.find(tag)
            if el is not None:
                outcome = tag if tag != "failure" else "failed"  # type: ignore[assignment]
                text = f"{el.get('message', '')}\n{el.text or ''}".strip()
                break
        cases.append(CaseOutcome(test_id, _file_of(classname, name), name, outcome, text))
    return cases


def _failure(test_id: str, message: str, text: str) -> Failure:
    m = _CRITERION.match(test_id.rsplit("::", 1)[-1])
    tail = "\n".join(text.strip().splitlines()[-TAIL_LINES:])[-TAIL_CHARS:]
    return Failure(
        test_id=test_id[:200] or "unknown",
        criterion_id=m.group(1) if m else None,
        message=(message.strip().splitlines() or [""])[0][:MESSAGE_CHARS],
        tail=tail,
    )


def _result(
    failures: list[Failure],
    n_passed: int,
    n_failed: int,
    frozen_ok: bool,
    duration_s: float,
    run_id: str,
    milestone_id: str,
) -> TestResult:
    return TestResult(
        created_by="harness",
        run_id=run_id,
        milestone_id=milestone_id,
        passed=n_failed == 0 and n_passed > 0 and frozen_ok,
        n_passed=n_passed,
        n_failed=n_failed,
        failures=failures[:MAX_FAILURES],
        frozen_ok=frozen_ok,
        duration_s=duration_s,
    )


def _from_cases(
    cases: list[CaseOutcome], frozen_ok: bool, duration_s: float, run_id: str, milestone_id: str
) -> TestResult:
    bad = [c for c in cases if c.outcome in ("failed", "error")]
    n_passed = sum(c.outcome == "passed" for c in cases)
    failures = [_failure(c.test_id, c.text.split("\n", 1)[0], c.text) for c in bad]
    return _result(failures, n_passed, len(bad), frozen_ok, duration_s, run_id, milestone_id)


def parse_junit(
    xml_text: str, *, frozen_ok: bool, duration_s: float, run_id: str, milestone_id: str
) -> TestResult:
    """JUnit XML (written by code we don't trust) → TestResult. No DTDs or entities allowed."""
    try:
        cases = parse_cases(xml_text)
    except UnsafeReport as e:
        f = _failure("junit", str(e), "")
        return _result([f], 0, 1, frozen_ok, duration_s, run_id, milestone_id)
    return _from_cases(cases, frozen_ok, duration_s, run_id, milestone_id)


def run_tests(
    ws: Workspace,
    sandbox: SandboxBackend,
    *,
    run_id: str,
    milestone_id: str,
    timeout_s: float,
    require_frozen: bool = True,
) -> TestResult:
    """Verify the frozen hash (raises FrozenTamper/NotFrozen), run pytest, parse its report.

    `require_frozen=False` is for red-before-green runs on stubs, before `freeze()`.
    """
    return run_tests_detailed(
        ws,
        sandbox,
        run_id=run_id,
        milestone_id=milestone_id,
        timeout_s=timeout_s,
        require_frozen=require_frozen,
    )[0]


def run_tests_detailed(
    ws: Workspace,
    sandbox: SandboxBackend,
    *,
    run_id: str,
    milestone_id: str,
    timeout_s: float,
    require_frozen: bool = True,
) -> tuple[TestResult, list[CaseOutcome]]:
    """Like `run_tests`, plus every test case's outcome (for red-green checks and digests).

    The frozen hash is verified before and after the run (raises FrozenTamper)."""
    frozen_ok = False
    if require_frozen:
        verify(ws)
        frozen_ok = True
    results = ws.root / RESULTS_DIR
    shutil.rmtree(results, ignore_errors=True)
    results.mkdir()
    report = f"{RESULTS_DIR}/junit.xml"
    res = sandbox.run(
        [
            "python",
            "-m",
            "pytest",
            "tests/frozen",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={report}",
            "-o",
            "junit_family=xunit1",
        ],
        timeout_s,
    )
    if require_frozen:
        verify(ws)  # again after the run: code under test must not rewrite tests while running
    meta = {"run_id": run_id, "milestone_id": milestone_id}
    if res.timed_out:
        f = _failure("pytest", f"test run timed out after {timeout_s:.0f} s", res.stdout)
        return _result([f], 0, 1, frozen_ok, res.duration_s, **meta), []
    path = ws.root / report
    if not path.is_file() or path.stat().st_size == 0:
        f = _failure(
            "pytest",
            f"pytest produced no report (exit {res.exit_code})",
            res.stdout + "\n" + res.stderr,
        )
        return _result([f], 0, 1, frozen_ok, res.duration_s, **meta), []
    try:
        cases = parse_cases(path.read_text(errors="replace"))
    except (UnsafeReport, ET.ParseError) as e:
        f = _failure("junit", f"unreadable JUnit report: {e}", res.stdout)
        return _result([f], 0, 1, frozen_ok, res.duration_s, **meta), []
    return _from_cases(cases, frozen_ok, res.duration_s, **meta), cases
