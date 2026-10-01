"""Templates test results and ledger data into a ResultDigest (D33: minimal, deterministic).

Phase 7 adds bench numbers, optimizer outcomes and the LLM-written deviations note.
"""

from __future__ import annotations

from collections.abc import Sequence

from r2r.contracts import CriterionResult, MilestoneSpec, ResultDigest
from r2r.harness.tests import CaseOutcome
from r2r.store.ledger import Ledger

DEVIATIONS_CHARS = 500


def build_digest(
    spec: MilestoneSpec,
    cases: Sequence[CaseOutcome],
    ledger: Ledger,
    *,
    run_id: str,
    milestone_id: str,
    test_files: Sequence[str] | None,
    runtime_baseline_s: float | None = None,
    runtime_final_s: float | None = None,
    optimizer_attempts: int = 0,
    optimizer_stop: str | None = None,
) -> ResultDigest:
    mine = [c for c in cases if test_files is None or c.file in test_files]
    criteria = []
    for crit in spec.acceptance_criteria:
        own = [c for c in mine if c.criterion_id == crit.id and c.outcome != "skipped"]
        ok = sum(c.outcome == "passed" for c in own)
        passed = bool(own) and ok == len(own)
        evidence = f"{ok}/{len(own)} tests pass" if own else "no tests for this criterion"
        criteria.append(CriterionResult(id=crit.id, passed=passed, evidence=evidence))
    n_pass = sum(c.outcome == "passed" for c in cases)
    n_fail = sum(c.outcome in ("failed", "error") for c in cases)
    n_ok = sum(c.passed for c in criteria)
    if n_ok == len(criteria) and n_fail == 0:
        status = "met"
    elif n_ok == 0:
        status = "failed"
    else:
        status = "partial"
    failed_ids = [c.id for c in criteria if not c.passed]
    deviations = ""
    if failed_ids:
        deviations = f"Criteria not met: {', '.join(failed_ids)}."
    if n_fail and not failed_ids:
        deviations = f"{n_fail} test(s) outside this milestone's criteria are failing."
    metrics = {"tests_passed": float(n_pass), "tests_failed": float(n_fail)}
    if runtime_baseline_s is not None:
        metrics["runtime_baseline_s"] = runtime_baseline_s
        metrics["runtime_final_s"] = runtime_final_s or runtime_baseline_s
        metrics["optimizer_attempts"] = float(optimizer_attempts)
    if optimizer_stop:
        deviations = f"{deviations} Optimizer stopped: {optimizer_stop}.".strip()
    rows = [r for r in ledger.rows(run_id) if r.milestone_id == milestone_id]
    return ResultDigest(
        created_by="digest",
        run_id=run_id,
        milestone_id=milestone_id,
        status=status,
        criteria=criteria,
        metrics=metrics,
        deviations=deviations[:DEVIATIONS_CHARS],
        cost_usd=ledger.spent(run_id, milestone_id),
        tokens=sum(r.input_tokens + r.output_tokens for r in rows),
    )
