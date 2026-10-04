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
        redgreen_ok=True,
        tests_passed=True,
        verification=_ver("verified"),
        auto_redirects=0,
        first_roadmap=False,
        design_feedback="",
    )
    return Facts(**{**base, **kw})


def test_config_defaults() -> None:
    assert not CFG.enabled and CFG.roadmap_first == "human" and CFG.publication == "human"
    assert CFG.max_auto_redirects == 2 and CFG.max_fix_rounds == 2


@pytest.mark.parametrize("gate", ["escalation", "coder", "dispute", "tamper", "budget"])
def test_always_human(gate: str) -> None:
    assert decide(gate, facts(), CFG) is None


def test_roadmap() -> None:
    assert decide("roadmap", facts(first_roadmap=True), CFG) is None  # you approve the plan once
    assert decide("roadmap", facts(), CFG).action == "approve"
    assert decide("roadmap", facts(flags=[SERIOUS]), CFG) is None
    auto_first = CFG.model_copy(update={"roadmap_first": "auto"})
    assert decide("roadmap", facts(first_roadmap=True), auto_first).action == "approve"


def test_tests_gate() -> None:
    d = decide("tests", facts(flags=[WARN]), CFG)
    assert d.action == "approve" and d.created_by == "harness" and d.note.startswith("autopilot:")
    d = decide("tests", facts(flags=[SERIOUS, WARN]), CFG)
    assert d.action == "redirect" and SERIOUS.text in d.note and WARN.text not in d.note
    assert decide("tests", facts(flags=[SERIOUS], auto_redirects=2), CFG) is None  # limit


def test_design_gate_always_redirects_until_limit() -> None:
    d = decide("design", facts(redgreen_ok=False, design_feedback="AC2 has no failing test."), CFG)
    assert d.action == "redirect" and "AC2 has no failing test." in d.note
    assert decide("design", facts(redgreen_ok=False, auto_redirects=2), CFG) is None


@pytest.mark.parametrize(
    ("kw", "approved"),
    [
        ({}, True),
        ({"verification": _ver("none")}, True),  # nothing to verify: tests decide
        ({"verification": _ver("unchecked_only")}, True),
        ({"verification": _ver("mismatch")}, False),
        ({"verification": _ver("error")}, False),
        ({"tests_passed": False}, False),
        ({"flags": [SERIOUS]}, False),
    ],
)
def test_result_gate(kw: dict, approved: bool) -> None:
    d = decide("result", facts(**kw), CFG)
    assert (d is not None and d.action == "approve") is approved
    if not approved:
        assert d is None


def test_nothing_checked_when_criteria_cite_recorded_data_goes_to_the_owner() -> None:
    """D84: 'verified' needs values actually compared when the milestone cites the owner's data."""
    for status in ("none", "unchecked_only"):
        assert decide("result", facts(verification=_ver(status), cites_data=True), CFG) is None
    assert decide("result", facts(cites_data=True), CFG).action == "approve"  # verified


def test_unchecked_criteria_go_to_the_owner() -> None:
    """D87: criteria with no numerical check are judged by the owner, so no automatic approval."""
    assert decide("result", facts(unchecked=["AC3"]), CFG) is None
    assert decide("result", facts(unchecked=[]), CFG).action == "approve"


def test_publication() -> None:
    assert decide("publication", facts(), CFG) is None
    assert (
        decide("publication", facts(), CFG.model_copy(update={"publication": "auto"})).action
        == "approve"
    )
