"""Red before green (D32): every criterion needs a test that fails on the stubs for the right
reason (NotImplementedError). Pure function over test-case outcomes and check results."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from r2r.harness.checks import CheckResult
from r2r.harness.tests import CaseOutcome

RIGHT_REASON = "NotImplementedError"
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
        return "\n".join(lines) or "All criteria are red for the right reason."


def assess(
    criteria: Sequence[str],
    cases: Sequence[CaseOutcome],
    checks: Sequence[CheckResult],
    test_files: Sequence[str] | None = None,
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
            broken[c.test_id] = c.text.strip().splitlines()[0][:EXCERPT_CHARS] if c.text else ""
    vacuous = [cid for cid in criteria if cid not in red]
    failing_checks = [c.file for c in checks if not c.passed]
    return RedGreen(
        ok=not vacuous and not broken and not failing_checks,
        vacuous=vacuous,
        broken=sorted(broken),
        failing_checks=failing_checks,
        broken_excerpts=broken,
    )
