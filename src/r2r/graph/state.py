"""Graph state: contracts, refs and counters only. Never raw dicts or transcripts."""

from __future__ import annotations

from typing import Literal, TypedDict

from r2r.contracts import (
    CoordinatorVerdict,
    HumanDecision,
    MathSpec,
    MilestoneSpec,
    OptimizationOutcome,
    Proposal,
    ResearchBrief,
    ResultDigest,
    Roadmap,
    TestResult,
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
    math_spec: MathSpec | None  # Phase 5
    test_result: TestResult | None  # Phase 5
    coder_sessions: int  # Phase 5
    opt_outcomes: list[OptimizationOutcome]  # Phase 6
    digest: ResultDigest | None
    human_decision: HumanDecision | None
    human_note: str | None  # a redirect note waiting for the long-term planner
