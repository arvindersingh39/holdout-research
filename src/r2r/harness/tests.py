"""Runs frozen tests and builds a TestResult (Phase 4).

Test names follow `test_<CRITERION>__<what>` (e.g. `test_AC2__put_call_parity`), so each
failure maps to an acceptance criterion without pytest plugins.
"""

from __future__ import annotations

import re
import shutil
import xml.etree.ElementTree as ET

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


def parse_junit(
    xml_text: str, *, frozen_ok: bool, duration_s: float, run_id: str, milestone_id: str
) -> TestResult:
    """JUnit XML (written by code we don't trust) → TestResult. No DTDs or entities allowed."""
    if len(xml_text) > MAX_REPORT_BYTES or "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        f = _failure("junit", "unsafe or oversized JUnit report rejected", "")
        return _result([f], 0, 1, frozen_ok, duration_s, run_id, milestone_id)
    root = ET.fromstring(xml_text)
    failures: list[Failure] = []
    n_passed = n_failed = 0
    for case in root.iter("testcase"):
        test_id = "::".join(x for x in (case.get("classname", ""), case.get("name", "")) if x)
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is not None:
            n_failed += 1
            failures.append(_failure(test_id, bad.get("message", ""), bad.text or ""))
        elif case.find("skipped") is None:
            n_passed += 1
    return _result(failures, n_passed, n_failed, frozen_ok, duration_s, run_id, milestone_id)


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
    meta = {"run_id": run_id, "milestone_id": milestone_id}
    if res.timed_out:
        f = _failure("pytest", f"test run timed out after {timeout_s:.0f} s", res.stdout)
        return _result([f], 0, 1, frozen_ok, res.duration_s, **meta)
    path = ws.root / report
    if not path.is_file() or path.stat().st_size == 0:
        f = _failure(
            "pytest",
            f"pytest produced no report (exit {res.exit_code})",
            res.stdout + "\n" + res.stderr,
        )
        return _result([f], 0, 1, frozen_ok, res.duration_s, **meta)
    try:
        return parse_junit(
            path.read_text(errors="replace"), frozen_ok=frozen_ok, duration_s=res.duration_s, **meta
        )
    except ET.ParseError as e:
        f = _failure("junit", f"unreadable JUnit report: {e}", res.stdout)
        return _result([f], 0, 1, frozen_ok, res.duration_s, **meta)
