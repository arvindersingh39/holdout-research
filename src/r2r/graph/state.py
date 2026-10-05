"""Graph state: contracts, refs and counters only. Never raw dicts or transcripts."""

from __future__ import annotations

from typing import Literal, TypedDict

from r2r.contracts import (
    CoordinatorVerdict,
    HumanDecision,
    MathSpec,
    MilestoneSpec,
    OversightFlag,
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
    stubs: list[dict]  # D93: the designer's stub functions {path, name, params}
    design_ok: bool
    design_attempts: int  # unusable designs for the current milestone
    design_feedback: str | None  # why the last design was unusable
    test_result: TestResult | None  # the stub check (D93)
    coder_sessions: int
    coder_message: str | None  # the coder's final words in its last session
    benchmark_path: str | None  # the results file the owner named at the benchmark gate
    benchmark_error: str | None  # why the last results file could not be used
    verification: VerificationReport | None  # the benchmark comparison (D93)
    fix_rounds: int  # benchmark mismatches sent back to the coder this milestone
    verify_retry: bool
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
    split_request: bool  # the milestone's spec could not fit: the long planner splits it
    spec_splits: dict[str, int]  # milestone id -> splits already requested (at most one)
    human_note: str | None  # a redirect note waiting for the long-term planner
    budget_blocked: str | None  # node that hit BudgetExceeded (D46)
    budget_message: str | None
    budget_retry: str | None  # where the budget gate sends the run next
