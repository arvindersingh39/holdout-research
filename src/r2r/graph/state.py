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
    math_spec: MathSpec | None
    design_attempts: int  # failed designs for the current milestone
    design_feedback: str | None  # why the last design was rejected (red-green or owner)
    redgreen_ok: bool
    frozen_commit: str | None  # workspace commit holding this milestone's frozen tests
    test_result: TestResult | None
    coder_sessions: int
    tamper: str | None  # FrozenTamper message waiting at the tamper gate
    tamper_attempts: int  # blocked writes to frozen files, over the whole run
    opt_outcomes: list[OptimizationOutcome]  # Phase 6
    digest: ResultDigest | None
    human_decision: HumanDecision | None
    human_note: str | None  # a redirect note waiting for the long-term planner
