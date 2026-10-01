"""#6 Optimizer: proposes OptimizationPatches from a ProfileReport (Phase 6).

It only proposes. Whether a patch is kept, reverted, or the loop stops is decided by the
harness (`harness.optimize.try_patch`, `harness.stoprule`), never by the model.
"""

from __future__ import annotations

from pydantic import BaseModel

from r2r.contracts import MathSpec, OptimizationOutcome, OptimizationPatch, ProfileReport
from r2r.llm.gateway import CallContext, call_structured

ROLE = "optimizer"


class AttemptNote(BaseModel):
    attempt: int
    technique: str
    rationale: str
    kept: bool
    reason: str


class OptimizerInput(BaseModel):
    profile: ProfileReport
    hotspot_source: str
    runtime_s: float
    target_runtime_s: float | None
    signatures: list[str]
    tolerances: dict[str, float]
    previous_attempts: list[AttemptNote]


def notes(
    patches: list[OptimizationPatch | None], outcomes: list[OptimizationOutcome]
) -> list[AttemptNote]:
    out = []
    for patch, o in zip(patches, outcomes, strict=True):
        out.append(
            AttemptNote(
                attempt=o.attempt,
                technique=patch.technique if patch else "none",
                rationale=patch.rationale if patch else "no valid patch produced",
                kept=o.accepted,
                reason=o.reason,
            )
        )
    return out


def run_optimizer(
    profile: ProfileReport,
    source: str,
    runtime_s: float,
    target_s: float | None,
    math_spec: MathSpec,
    previous: list[AttemptNote],
    ctx: CallContext,
) -> OptimizationPatch:
    payload = OptimizerInput(
        profile=profile,
        hotspot_source=source,
        runtime_s=runtime_s,
        target_runtime_s=target_s,
        signatures=math_spec.signatures,
        tolerances=math_spec.tolerances,
        previous_attempts=previous,
    )
    return call_structured(ROLE, ROLE, payload, OptimizationPatch, ctx)
