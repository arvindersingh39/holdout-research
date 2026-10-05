"""Minimal valid instances of every contract, shared by unit tests."""

from r2r.contracts import (
    BenchSpec,
    BlobRef,
    Citation,
    CoordinatorVerdict,
    Criterion,
    CriterionResult,
    Failure,
    FileSpec,
    Hotspot,
    HumanDecision,
    Issue,
    MathDesign,
    MathSpec,
    Milestone,
    MilestoneSpec,
    OptimizationOutcome,
    OptimizationPatch,
    PaperCard,
    ProfileReport,
    Proposal,
    PublicationBundle,
    ResearchBrief,
    ResultDigest,
    Roadmap,
    TestResult,
)

RUN = "run-test"
META = {"run_id": RUN}


def blob_ref(n: int = 0, kind: str = "paper") -> BlobRef:
    return BlobRef(blob_id=f"{n:064x}", kind=kind, summary="a blob", est_tokens=10)


def criterion(i: int = 1) -> Criterion:
    return Criterion(id=f"AC{i}", text=f"criterion {i}")


def proposal() -> Proposal:
    return Proposal(created_by="cli", **META, title="MC pricer", text_ref=blob_ref(1, "proposal"))


def paper_card() -> PaperCard:
    return PaperCard(
        created_by="retriever",
        **META,
        citation=Citation(
            key="glasserman2003",
            entry_type="book",
            title="Monte Carlo Methods",
            authors=["Paul Glasserman"],
            year=2003,
        ),
        problem="Pricing derivatives by simulation.",
        method="Monte Carlo with variance reduction.",
        key_equations=[r"S_T = S_0 e^{(r - \sigma^2/2)T + \sigma W_T}"],
        data="Synthetic GBM paths.",
        results="Control variates cut variance.",
        relevance="Core reference for the pilot.",
    )


def research_brief() -> ResearchBrief:
    return ResearchBrief(
        created_by="retriever",
        **META,
        objective="Fast variance-reduced MC pricing of European options.",
        hypotheses=["Control variates beat antithetic variates."],
        methods=["Monte Carlo", "Black-Scholes"],
        success_metrics=["SE slope within 0.1 of -0.5"],
        paper_cards=[blob_ref(2)],
    )


def milestone(i: int = 1, depends_on: list[str] | None = None) -> Milestone:
    return Milestone(
        id=f"M{i}",
        title=f"milestone {i}",
        acceptance_criteria=[criterion(1)],
        depends_on=depends_on or [],
    )


def roadmap() -> Roadmap:
    return Roadmap(
        created_by="long_planner",
        **META,
        version=1,
        goal="Price European options fast and accurately.",
        milestones=[milestone(1), milestone(2, ["M1"])],
        current_milestone_id="M1",
        rationale="Baseline first, then variance reduction.",
    )


def milestone_spec() -> MilestoneSpec:
    return MilestoneSpec(
        created_by="short_planner",
        run_id=RUN,
        milestone_id="M1",
        tasks=["Implement the Black-Scholes closed form."],
        interfaces=[
            "bs_call(S: float, K: float, T: float [years], r: float, sigma: float) -> float"
        ],
        acceptance_criteria=[criterion(1)],
        rationale="Closed form is the oracle for every later test.",
    )


def coordinator_verdict() -> CoordinatorVerdict:
    return CoordinatorVerdict(
        created_by="coordinator",
        run_id=RUN,
        milestone_id="M1",
        verdict="revise",
        issues=[Issue(target="spec", severity="high", text="Units missing for sigma.")],
    )


def math_spec() -> MathSpec:
    return MathSpec(
        created_by="math_designer",
        run_id=RUN,
        milestone_id="M1",
        formulation=r"C = S N(d_1) - K e^{-rT} N(d_2)",
        algorithm="Evaluate the closed form with scipy.stats.norm.",
        signatures=["bs_call(S, K, T, r, sigma) -> float"],
        tolerances={"price_abs": 1e-8},
        bench=BenchSpec(entrypoint="bench/run.py", problem_size="1e6 paths", target_runtime_s=0.5),
    )


def math_design() -> MathDesign:
    return MathDesign(
        created_by="math_designer",
        run_id=RUN,
        milestone_id="M1",
        spec=math_spec(),
        files=[
            FileSpec(
                path="src/bs/core.py",
                purpose="stub",
                content="def bs_call(S, K, T, r, sigma):\n    raise NotImplementedError\n",
            ),
            FileSpec(path="bench/run.py", purpose="bench", content="print('bench')"),
        ],
    )


def make_test_result() -> TestResult:
    return TestResult(
        created_by="harness",
        run_id=RUN,
        milestone_id="M1",
        passed=False,
        n_passed=3,
        n_failed=1,
        frozen_ok=True,
        duration_s=1.5,
        failures=[
            Failure(
                test_id="test_bs::test_atm",
                criterion_id="AC1",
                message="assert 10.4 == 10.45",
                tail="E   assert 10.4 == 10.45",
            )
        ],
    )


def profile_report() -> ProfileReport:
    return ProfileReport(
        created_by="harness",
        run_id=RUN,
        milestone_id="M1",
        total_s=2.0,
        hotspots=[
            Hotspot(
                qualname="mc.price",
                file="src/mc.py",
                line_start=10,
                line_end=40,
                cum_s=1.8,
                pct=90.0,
            )
        ],
    )


def optimization_patch() -> OptimizationPatch:
    return OptimizationPatch(
        created_by="optimizer",
        run_id=RUN,
        milestone_id="M1",
        diff="--- a/src/mc.py\n+++ b/src/mc.py\n@@ -1 +1 @@\n-x\n+y\n",
        technique="vectorize",
        rationale="Replace the Python loop with numpy.",
    )


def optimization_outcome() -> OptimizationOutcome:
    return OptimizationOutcome(
        created_by="harness",
        run_id=RUN,
        milestone_id="M1",
        attempt=1,
        accepted=True,
        reason="12% faster, tests green",
        runtime_before_s=1.0,
        runtime_after_s=0.88,
        sloc_before=120,
        sloc_after=118,
    )


def result_digest() -> ResultDigest:
    return ResultDigest(
        created_by="digest",
        run_id=RUN,
        milestone_id="M1",
        status="met",
        criteria=[CriterionResult(id="AC1", passed=True, evidence="all 12 tests pass")],
        metrics={"runtime_s": 0.41},
        cost_usd=0.42,
        tokens=12000,
    )


def human_decision() -> HumanDecision:
    return HumanDecision(created_by="human", **META, action="approve", note="Looks good.")


def publication_bundle() -> PublicationBundle:
    return PublicationBundle(
        created_by="writer",
        **META,
        mode="paper",
        artifacts=[blob_ref(3, "figure")],
        build_ok=True,
        log_tail="Output written on paper.pdf",
    )


ALL_SAMPLES = [
    proposal,
    paper_card,
    research_brief,
    roadmap,
    milestone_spec,
    coordinator_verdict,
    math_spec,
    math_design,
    make_test_result,
    profile_report,
    optimization_patch,
    optimization_outcome,
    result_digest,
    human_decision,
    publication_bundle,
]
