"""Graph state: contracts, refs and counters only. Never raw dicts or transcripts."""

from __future__ import annotations

from typing import Literal, TypedDict

from r2r.contracts import (
    BaselineCard,
    CoordinatorVerdict,
    HumanDecision,
    MathSpec,
    MilestoneSpec,
    OptimizationOutcome,
    OptimizationPatch,
    OversightFlag,
    ProfileReport,
    Proposal,
    PublicationBundle,
    ResearchBrief,
    ResultDigest,
    Roadmap,
    TestResult,
    VerificationReport,
)


class PipelineState(TypedDict, total=False):
    run_id: str
    status: Literal["running", "complete", "stopped"]
    cycle: int
    proposal: Proposal
    brief: ResearchBrief
    roadmap: Roadmap
    approved_roadmap: Roadmap | None  # last roadmap a human saw; drives material-change gates
    spec: MilestoneSpec | None
    verdict: CoordinatorVerdict | None
    revise_rounds: int
    math_spec: MathSpec | None
    design_attempts: int  # failed designs for the current milestone
    design_feedback: str | None  # why the last design was rejected (red-green or owner)
    redgreen_ok: bool
    frozen_commit: str | None  # workspace commit holding this milestone's frozen tests
    test_result: TestResult | None
    coder_sessions: int
    tamper: str | None  # FrozenTamper message waiting at the tamper gate
    tamper_attempts: int  # blocked writes to frozen files, over the whole run
    baselines: list[BaselineCard]  # quote-verified published values for this milestone (D52)
    baseline_note: str | None
    perf_unmet: list[str]  # perf tests the reference misses; the coder must meet them (D60)
    dispute: str | None  # the coder's dispute of a frozen test, rendered (D56)
    dispute_file: str | None
    coder_message: str | None  # the coder's final words in its last session
    opt_outcomes: list[OptimizationOutcome]
    opt_patches: list[OptimizationPatch | None]  # aligned with opt_outcomes; None = no valid patch
    opt_baseline_s: float | None
    opt_runtime_s: float | None  # current best
    opt_sloc: int
    opt_bench_hash: str | None
    opt_profile: ProfileReport | None
    opt_stop_reason: str | None
    digest: ResultDigest | None
    human_decision: HumanDecision | None
    briefings: dict[str, str]  # role -> the overseer's active briefing (Phase 7c)
    carry_briefings: dict[
        str, str
    ]  # planner briefings from a result review, for the next milestone
    flags: list[OversightFlag]  # overseer flags for this milestone, shown at gates
    oversee_calls: int  # overseer calls for the current milestone
    publication: list[PublicationBundle]  # Phase 8: one per mode
    publication_md: str | None  # what the publication gate shows
    publish_note: str | None  # the owner's revision request
    publish_revisions: int
    verification: VerificationReport | None  # Phase 7d: what the verifier measured
    fix_rounds: int  # verification mismatches sent back to the coder this milestone
    verify_retry: bool
    split_request: bool  # the milestone's spec could not fit: the long planner splits it
    spec_splits: dict[str, int]  # milestone id -> splits already requested (at most one)
    human_note: str | None  # a redirect note waiting for the long-term planner
    budget_blocked: str | None  # node that hit BudgetExceeded (D46)
    budget_message: str | None
    budget_retry: str | None  # where the budget gate sends the run next
