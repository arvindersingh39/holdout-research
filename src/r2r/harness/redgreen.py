"""Red before green (D32): every criterion needs a test that fails on the stubs for the right
reason (NotImplementedError). Pure function over test-case outcomes and check results."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from r2r.harness.checks import CheckResult
from r2r.harness.tests import CaseOutcome

RIGHT_REASON = "NotImplementedError"
_UNCHECKED = object()  # reference check not requested (callers predating D54)
EXCERPT_CHARS = 200


@dataclass(frozen=True)
class RedGreen:
    ok: bool
    vacuous: list[str] = field(
        default_factory=list
    )  # criteria with no red-for-the-right-reason test
    broken: list[str] = field(default_factory=list)  # tests failing for another reason
    failing_checks: list[str] = field(default_factory=list)
    broken_excerpts: dict[str, str] = field(default_factory=dict)
    unsatisfiable: list[str] = field(default_factory=list)  # fail even on the reference (D54)
    no_reference: bool = False
    perf_unmet: list[str] = field(default_factory=list)  # perf targets the reference misses (D60)
    reference_excerpts: dict[str, str] = field(default_factory=dict)  # why each failed (D61)

    def describe(self) -> str:
        """Feedback for the math designer (or the human at the design gate)."""
        lines = []
        if self.vacuous:
            ids = ", ".join(self.vacuous)
            lines.append(f"Criteria with no test failing on the stubs with {RIGHT_REASON}: {ids}")
        for test_id in self.broken:
            lines.append(
                f"Test fails for the wrong reason: {test_id}: "
                f"{self.broken_excerpts.get(test_id, '')}"
            )
        if self.failing_checks:
            lines.append("SymPy checks failing: " + ", ".join(self.failing_checks))
        if self.no_reference:
            lines.append("No reference solution was provided (files with purpose 'reference').")
        if self.unsatisfiable:
            lines.append("Tests that fail on your own reference solution:")
            for test_id in self.unsatisfiable:
                lines.append(f"- {test_id}: {self.reference_excerpts.get(test_id, '')}")
            lines.append(
                "Either the test or your reference is wrong: check the reference first (e.g. "
                "integer dtype overflow, edge cases), then the test. Never loosen a criterion's "
                "numbers to make a test pass."
            )
        return "\n".join(lines) or "All criteria are red for the right reason."


def assess(
    criteria: Sequence[str],
    cases: Sequence[CaseOutcome],
    checks: Sequence[CheckResult],
    test_files: Sequence[str] | None = None,
    reference_cases: Sequence[CaseOutcome] | object | None = _UNCHECKED,
) -> RedGreen:
    """`test_files` limits the cases to this milestone's files (earlier milestones' tests pass)."""
    mine = [c for c in cases if test_files is None or c.file in test_files]
    red: set[str] = set()
    broken: dict[str, str] = {}
    for c in mine:
        if c.outcome not in ("failed", "error"):
            continue
        if RIGHT_REASON in c.text:
            if c.criterion_id:
                red.add(c.criterion_id)
        else:
            broken[c.test_id] = _first_line(c.text)
    vacuous = [cid for cid in criteria if cid not in red]
    failing_checks = [c.file for c in checks if not c.passed]
    no_reference = reference_cases is None
    unsatisfiable: list[str] = []
    perf_unmet: list[str] = []
    reference_excerpts: dict[str, str] = {}
    if isinstance(reference_cases, list | tuple):
        missed = [c for c in reference_cases if c.misses_reference]
        unsatisfiable = sorted(c.test_id for c in missed)
        reference_excerpts = {c.test_id: _first_line(c.text) for c in missed}
        perf_unmet = sorted(
            c.test_id for c in reference_cases if c.outcome == "failed" and c.is_perf
        )
    return RedGreen(
        ok=not vacuous
        and not broken
        and not failing_checks
        and not no_reference
        and not unsatisfiable,
        unsatisfiable=unsatisfiable,
        perf_unmet=perf_unmet,
        reference_excerpts=reference_excerpts,
        no_reference=no_reference,
        vacuous=vacuous,
        broken=sorted(broken),
        failing_checks=failing_checks,
        broken_excerpts=broken,
    )


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0][:EXCERPT_CHARS] if text.strip() else ""
