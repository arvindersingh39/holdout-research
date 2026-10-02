"""Every Pydantic contract passed between agents and graph nodes (PLAN.md §6)."""

from r2r.contracts.base import (
    Contract,
    ContractTooLarge,
    Part,
    Role,
    ensure_within_cap,
    get_contract_caps,
    set_contract_caps,
)
from r2r.contracts.common import BlobKind, BlobRef, Criterion, Failure, FileSpec, Hotspot, Issue
from r2r.contracts.harness import OptimizationOutcome, OptimizationPatch, ProfileReport, TestResult
from r2r.contracts.math import BenchSpec, MathDesign, MathSpec
from r2r.contracts.oversight import Briefing, OversightFlag, OversightReport
from r2r.contracts.planning import (
    BaselineCard,
    Citation,
    CoordinatorVerdict,
    Milestone,
    MilestoneSpec,
    PaperCard,
    Proposal,
    ResearchBrief,
    Roadmap,
)
from r2r.contracts.results import CriterionResult, HumanDecision, PublicationBundle, ResultDigest

__all__ = [
    "BaselineCard",
    "BenchSpec",
    "BlobKind",
    "BlobRef",
    "Briefing",
    "Citation",
    "Contract",
    "ContractTooLarge",
    "CoordinatorVerdict",
    "Criterion",
    "CriterionResult",
    "Failure",
    "FileSpec",
    "Hotspot",
    "HumanDecision",
    "Issue",
    "MathDesign",
    "MathSpec",
    "Milestone",
    "MilestoneSpec",
    "OptimizationOutcome",
    "OptimizationPatch",
    "OversightFlag",
    "OversightReport",
    "PaperCard",
    "Part",
    "ProfileReport",
    "Proposal",
    "PublicationBundle",
    "ResearchBrief",
    "ResultDigest",
    "Roadmap",
    "Role",
    "TestResult",
    "ensure_within_cap",
    "get_contract_caps",
    "set_contract_caps",
]
