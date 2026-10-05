"""Phase 7d autopilot (D76): plain-Python gate decisions from measurements and overseer flags.
None means "ask the human"."""

from pathlib import Path

import pytest

from r2r.contracts import OversightFlag, VerificationReport
from r2r.harness.autopilot import Facts, decide
from r2r.settings import load_settings

REPO = Path(__file__).resolve().parents[2]
CFG = load_settings(REPO / "tests" / "fixtures" / "config", None).budgets.autopilot
SERIOUS = OversightFlag(
    severity="high", text="Tests expect errors from working code.", evidence=["E1"]
)
WARN = OversightFlag(severity="warn", text="Coverage is thin.", evidence=["E2"])


def _ver(status: str) -> VerificationReport:
    return VerificationReport(created_by="harness", run_id="r", milestone_id="M1", status=status)


def facts(**kw) -> Facts:
    base = dict(
        run_id="r",
        milestone_id="M1",
        flags=[],
        tests_passed=True,
        verification=None,
        first_roadmap=False,
    )
    return Facts(**{**base, **kw})


def test_config_defaults() -> None:
    assert not CFG.enabled and CFG.roadmap_first == "human" and CFG.publication == "human"
    assert CFG.max_fix_rounds == 2


@pytest.mark.parametrize("gate", ["coder", "benchmark", "budget"])
def test_always_human(gate: str) -> None:
    """D93: a stuck coder, a benchmark run (the owner runs it) and money are the owner's."""
    assert decide(gate, facts(), CFG) is None


def test_roadmap() -> None:
    assert decide("roadmap", facts(first_roadmap=True), CFG) is None  # you approve the plan once
    assert decide("roadmap", facts(), CFG).action == "approve"
    assert decide("roadmap", facts(flags=[SERIOUS]), CFG) is None
    auto_first = CFG.model_copy(update={"roadmap_first": "auto"})
    assert decide("roadmap", facts(first_roadmap=True), auto_first).action == "approve"


@pytest.mark.parametrize(
    ("kw", "approved"),
    [
        ({}, True),  # complete code, no benchmarks
        ({"flags": [WARN]}, True),
        ({"tests_passed": False}, False),
        ({"flags": [SERIOUS]}, False),
        ({"has_benchmarks": True, "verification": _ver("verified")}, True),
        ({"has_benchmarks": True, "verification": _ver("mismatch")}, False),
        ({"has_benchmarks": True, "verification": _ver("error")}, False),
        ({"has_benchmarks": True, "verification": _ver("unchecked_only")}, False),
        ({"has_benchmarks": True, "verification": None}, False),
    ],
)
def test_result_gate(kw: dict, approved: bool) -> None:
    """D84/D93: a milestone with values to reproduce is approved only when they were."""
    d = decide("result", facts(**kw), CFG)
    assert (d is not None and d.action == "approve") is approved
    if approved:
        assert d.note.startswith("autopilot:") and d.created_by == "harness"
    else:
        assert d is None


def test_publication() -> None:
    assert decide("publication", facts(), CFG) is None
    assert (
        decide("publication", facts(), CFG.model_copy(update={"publication": "auto"})).action
        == "approve"
    )
