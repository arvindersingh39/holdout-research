"""Contracts: round-trip, field limits, token caps, cross-field rules."""

import re
from collections.abc import Callable, Iterator

import pytest
from pydantic import BaseModel, ValidationError
from samples import (
    ALL_SAMPLES,
    RUN,
    blob_ref,
    coordinator_verdict,
    criterion,
    make_test_result,
    math_design,
    math_spec,
    milestone,
    milestone_spec,
    optimization_patch,
    roadmap,
)

from r2r.contracts import (
    BlobRef,
    Contract,
    ContractTooLarge,
    CoordinatorVerdict,
    Failure,
    HumanDecision,
    MathDesign,
    MilestoneSpec,
    OptimizationPatch,
    Roadmap,
    ensure_within_cap,
    set_contract_caps,
)


@pytest.fixture
def caps() -> Iterator[Callable[[dict[str, int]], None]]:
    yield lambda c: set_contract_caps(c)
    set_contract_caps(None)  # back to budgets.yaml


@pytest.mark.parametrize("make", ALL_SAMPLES, ids=lambda f: f.__name__)
def test_round_trip(make: Callable[[], Contract]) -> None:
    c = make()
    assert isinstance(c, Contract)
    assert type(c).model_validate_json(c.model_dump_json()) == c


@pytest.mark.parametrize("make", ALL_SAMPLES, ids=lambda f: f.__name__)
def test_extra_fields_rejected(make: Callable[[], Contract]) -> None:
    data = make().model_dump()
    data["surprise"] = 1
    with pytest.raises(ValidationError):
        type(make()).model_validate(data)


def test_unknown_creator_rejected() -> None:
    data = roadmap().model_dump()
    data["created_by"] = "stranger"
    with pytest.raises(ValidationError):
        Roadmap.model_validate(data)


def test_blob_ref_rules() -> None:
    assert blob_ref().blob_id == "0" * 64
    with pytest.raises(ValidationError):
        BlobRef(blob_id="nothex", kind="paper", summary="x", est_tokens=1)
    with pytest.raises(ValidationError):
        BlobRef(blob_id="a" * 64, kind="paper", summary="x" * 301, est_tokens=1)


def test_string_limits() -> None:
    with pytest.raises(ValidationError):
        roadmap().model_copy(update={}).model_validate(
            {**roadmap().model_dump(), "rationale": "x" * 1601}
        )
    with pytest.raises(ValidationError):
        Roadmap.model_validate({**roadmap().model_dump(), "decisions_log": ["d"] * 11})
    with pytest.raises(ValidationError):
        Roadmap.model_validate({**roadmap().model_dump(), "decisions_log": ["x" * 401]})
    with pytest.raises(ValidationError):
        MilestoneSpec.model_validate({**milestone_spec().model_dump(), "rationale": "x" * 601})
    with pytest.raises(ValidationError):
        HumanDecision.model_validate(
            {"created_by": "human", "run_id": RUN, "action": "approve", "note": "x" * 1001}
        )


def test_line_limits() -> None:
    long_diff = "".join(f"+line {i}\n" for i in range(301))
    with pytest.raises(ValidationError, match="300 lines"):
        OptimizationPatch.model_validate({**optimization_patch().model_dump(), "diff": long_diff})
    with pytest.raises(ValidationError, match="15 lines"):
        Failure(test_id="t", message="m", tail="\n".join(["x"] * 16))


def test_roadmap_integrity() -> None:
    base = roadmap().model_dump()
    with pytest.raises(ValidationError, match="current_milestone_id"):
        Roadmap.model_validate({**base, "current_milestone_id": "M9"})
    with pytest.raises(ValidationError, match="depends_on"):
        Roadmap.model_validate({**base, "milestones": [milestone(1, ["M7"]).model_dump()]})
    with pytest.raises(ValidationError, match="duplicate"):
        Roadmap.model_validate(
            {**base, "milestones": [milestone(1).model_dump(), milestone(1).model_dump()]}
        )


def test_criterion_ids() -> None:
    assert criterion(3).id == "AC3"
    with pytest.raises(ValidationError):
        type(criterion())(id="X1", text="t")


def test_milestone_scoped_contracts_need_milestone_id() -> None:
    with pytest.raises(ValidationError, match="milestone_id"):
        MilestoneSpec.model_validate({**milestone_spec().model_dump(), "milestone_id": None})


def test_revise_needs_issues() -> None:
    with pytest.raises(ValidationError, match="issue"):
        CoordinatorVerdict.model_validate({**coordinator_verdict().model_dump(), "issues": []})
    ok = CoordinatorVerdict.model_validate(
        {**coordinator_verdict().model_dump(), "verdict": "pass", "issues": []}
    )
    assert ok.verdict == "pass"


def test_human_edit_needs_replacement() -> None:
    with pytest.raises(ValidationError, match="edited"):
        HumanDecision(created_by="human", run_id=RUN, action="edit", note="change M2")
    d = HumanDecision(created_by="human", run_id=RUN, action="edit", note="x", edited=roadmap())
    assert isinstance(d.edited, Roadmap)
    again = HumanDecision.model_validate_json(d.model_dump_json())
    assert isinstance(again.edited, Roadmap)


def test_math_design_files_cover_spec() -> None:
    assert math_design().spec == math_spec()
    data = math_design().model_dump()
    data["files"] = [f for f in data["files"] if f["path"] != "bench/run.py"]
    with pytest.raises(ValidationError, match=re.escape("bench/run.py")):
        MathDesign.model_validate(data)


def test_math_design_writes_stubs_only() -> None:
    """D93: no tests, checks, verify scripts or data from the designer."""
    for path in ("tests/frozen/sanity/test_sanity_x.py", "checks/c.py", "tests/frozen/data/x.json"):
        data = math_design().model_dump()
        data["files"].append({"path": path, "purpose": "stub", "content": "x"})
        with pytest.raises(ValidationError, match="stubs only"):
            MathDesign.model_validate(data)


def test_math_spec_schema_hides_the_retired_check_fields() -> None:
    """D93: the model never sees sanity or contract checks; older runs' specs still load."""
    from r2r.contracts import MathSpec

    props = MathSpec.model_json_schema()["properties"]
    for old in ("test_files", "sanity_checks", "contract_checks", "unchecked_criteria"):
        assert old not in props
    legacy = {**math_spec().model_dump(), "test_files": ["tests/frozen/sanity/test_sanity_a.py"]}
    legacy["unchecked_criteria"] = {"AC2": "a file"}
    assert MathSpec.model_validate(legacy).unchecked_criteria == {"AC2": "a file"}


def test_milestone_benchmarks_and_brief_benchmarks() -> None:
    from r2r.contracts import Benchmark, Milestone

    m = Milestone.model_validate(
        {**roadmap().milestones[0].model_dump(), "benchmarks": ["benchmarks.BM1", "graph0.B1[2]"]}
    )
    assert m.benchmarks == ["benchmarks.BM1", "graph0.B1[2]"]
    with pytest.raises(ValidationError):
        Milestone.model_validate({**m.model_dump(), "benchmarks": ["no spaces allowed"]})
    b = Benchmark(id="BM1", quantity="q", value=0.4, tolerance=0.05, source="proposal", quote="0.4")
    assert not b.verified  # set by code only
    with pytest.raises(ValidationError):
        Benchmark.model_validate({**b.model_dump(), "id": "X1"})


def test_file_spec_paths_stay_inside_workspace() -> None:
    for bad in ["../escape.py", "/abs/path.py"]:
        data = math_design().model_dump()
        data["files"][0]["path"] = bad
        with pytest.raises(ValidationError):
            MathDesign.model_validate(data)


def test_cap_from_budgets_yaml() -> None:
    # Roadmap cap is 2000 tokens in config/budgets.yaml; a sample is far below it.
    assert roadmap().token_estimate() < 2000


def test_cap_violation_raises(caps) -> None:
    caps({"TestResult": 20})
    with pytest.raises(ContractTooLarge) as e:
        make_test_result()
    assert e.value.contract == "TestResult"
    assert e.value.cap == 20
    assert e.value.tokens > 20


def test_contracts_without_cap_are_unbounded(caps) -> None:
    caps({})
    assert make_test_result().n_failed == 1


def test_ensure_within_cap_uses_model_tokenizer(caps) -> None:
    rm = roadmap()
    caps({"Roadmap": 10_000})
    assert ensure_within_cap(rm, model="anthropic/claude-sonnet-5-5") == rm
    caps({"Roadmap": 5})
    with pytest.raises(ContractTooLarge):
        ensure_within_cap(rm, model="anthropic/claude-sonnet-5-5")


def test_every_cap_names_a_contract() -> None:
    import r2r.contracts as c
    from r2r.settings import load_settings

    for name in load_settings().budgets.contract_token_caps:
        cls = getattr(c, name)
        assert isinstance(cls, type) and issubclass(cls, BaseModel), name


def test_baseline_card() -> None:
    from r2r.contracts import BaselineCard

    card = BaselineCard(
        created_by="scout",
        run_id="r",
        milestone_id="M1",
        criterion_id="AC1",
        claim="ATM call price",
        value=10.4506,
        units="USD",
        precision="4 dp",
        source_url="https://example.org/bs",
        source_title="BS table",
        quote="the call price is 10.4506",
        license_note="",
        verified=True,
    )
    assert BaselineCard.model_validate_json(card.model_dump_json()) == card
    with pytest.raises(ValidationError):
        BaselineCard.model_validate({**card.model_dump(), "quote": "x" * 401})
    with pytest.raises(ValidationError):
        BaselineCard.model_validate({**card.model_dump(), "source_url": "ftp://nope"})


def test_file_spec_reference_purpose() -> None:
    from r2r.contracts import FileSpec

    assert FileSpec(path="src/pkg/core.py", purpose="reference", content="x").purpose == "reference"


# --- D94: one-round reflection -----------------------------------------------------------------


def _item(i: int, severity: str = "high") -> dict:
    return {"id": f"C{i}", "severity": severity, "text": f"fix {i}", "evidence": ["E1"]}


def test_critique_items_have_unique_ids_and_a_cap() -> None:
    from r2r.contracts import Critique

    meta = {"created_by": "overseer", "run_id": "r"}
    c = Critique(**meta, point="plan", items=[_item(1), _item(2, "warn")])
    assert [i.id for i in c.items] == ["C1", "C2"] and c.high() == ["C1"]
    with pytest.raises(ValidationError, match="unique"):
        Critique(**meta, point="plan", items=[_item(1), _item(1)])
    with pytest.raises(ValidationError):
        Critique(**meta, point="plan", items=[{**_item(1), "id": "X1"}])
    with pytest.raises(ValidationError):
        Critique(**meta, point="plan", items=[_item(i) for i in range(1, 12)])
    with pytest.raises(ValidationError):
        Critique(**meta, point="code", items=[])  # only the plan and the design are critiqued


def test_revisions_answer_every_item() -> None:
    from r2r.contracts import CritiqueResponse, RoadmapRevision, unanswered

    r = RoadmapRevision(
        created_by="long_planner",
        run_id="r",
        roadmap=roadmap(),
        responses=[CritiqueResponse(id="C1", verdict="changed", text="M1 split in two")],
    )
    assert unanswered(r.responses, ["C1"]) == [] and unanswered(r.responses, ["C1", "C2"]) == ["C2"]
    with pytest.raises(ValidationError):
        CritiqueResponse(id="C1", verdict="ignored", text="x")


def test_command_line_scripts_may_be_designed() -> None:
    """D97: scripts/ holds command-line entry points (both models reach for it)."""
    data = math_design().model_dump()
    data["files"].append({"path": "scripts/report.py", "purpose": "stub", "content": "x = 1\n"})
    assert MathDesign.model_validate(data).files[-1].path == "scripts/report.py"
