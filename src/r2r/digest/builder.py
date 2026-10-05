"""Templates the code check, the benchmark comparison and ledger data into a ResultDigest
(D33: minimal, deterministic; D93: no tests, so criteria are the owner's to judge unless the
milestone's benchmarks were reproduced)."""

from __future__ import annotations

from pydantic import BaseModel

from r2r.contracts import (
    CriterionResult,
    Failure,
    MilestoneSpec,
    ResultDigest,
    TestResult,
    VerificationReport,
)
from r2r.contracts.base import Text
from r2r.llm.gateway import CallContext, call_structured
from r2r.store.ledger import Ledger

DEVIATIONS_CHARS = 500


def build_digest(
    spec: MilestoneSpec,
    check: TestResult,
    verification: VerificationReport | None,
    ledger: Ledger,
    *,
    run_id: str,
    milestone_id: str,
) -> ResultDigest:
    ver = verification.status if verification is not None else "none"
    if not check.passed:
        evidence = f"code incomplete: {check.n_failed} stub check failure(s)"
    elif ver == "none":
        evidence = "implemented; no benchmark: you judge"
    else:
        evidence = f"implemented; benchmarks {ver}"
    criteria = [
        CriterionResult(id=c.id, passed=check.passed, evidence=evidence[:120], checked=False)
        for c in spec.acceptance_criteria
    ]
    if not check.passed:
        status = "failed" if check.n_passed == 0 else "partial"
        deviations = f"The code check still fails ({check.n_failed} item(s))."
    elif ver in ("mismatch", "error"):
        status = "partial"
        deviations = f"Benchmarks {ver}."
    else:
        status = "met"
        deviations = ""
    metrics = {"check_passed": float(check.n_passed), "check_failed": float(check.n_failed)}
    if verification is not None:
        metrics |= {
            "benchmarks_checked": float(verification.n_checked),
            "benchmarks_within": float(verification.n_within),
            "benchmarks_mismatched": float(verification.n_mismatched),
        }
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


class DeviationInput(BaseModel):
    criteria: list[CriterionResult]
    criteria_text: dict[str, str]
    failures: list[Failure]


class DeviationNote(BaseModel):
    text: Text(DEVIATIONS_CHARS, 1)


def write_deviations(
    spec: MilestoneSpec, digest: ResultDigest, result: TestResult, ctx: CallContext
) -> str:
    """D45: a short LLM note on what deviated, only called when a criterion failed."""
    payload = DeviationInput(
        criteria=digest.criteria,
        criteria_text={c.id: c.text for c in spec.acceptance_criteria},
        failures=result.failures,
    )
    return call_structured("digest", "digest", payload, DeviationNote, ctx).text
