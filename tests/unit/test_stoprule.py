"""Optimizer stop rule: keep/revert per attempt and when to stop. Pure functions."""

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from r2r.contracts import OptimizationOutcome
from r2r.harness.stoprule import accept, decide, gain
from r2r.settings import BudgetsConfig, load_settings

REPO = Path(__file__).resolve().parents[2]
# min_gain 5%, patience 2, max attempts 4
CFG: BudgetsConfig = load_settings(REPO / "config", env_file=None).budgets


def _outcome(n: int, before: float, after: float | None, accepted: bool) -> OptimizationOutcome:
    return OptimizationOutcome(
        created_by="harness",
        run_id="r",
        milestone_id="M1",
        attempt=n,
        accepted=accepted,
        reason="x",
        runtime_before_s=before,
        runtime_after_s=after,
        sloc_before=100,
        sloc_after=100,
    )


@pytest.mark.parametrize(
    ("before", "after", "tests_ok", "sloc_b", "sloc_a", "ok", "why"),
    [
        (1.0, 0.90, True, 100, 100, True, "faster"),
        (1.0, 0.95, True, 100, 100, True, "faster"),  # exactly min_gain
        (1.0, 0.97, True, 100, 100, False, "below"),
        (1.0, 0.50, False, 100, 100, False, "tests"),
        (1.0, 1.01, True, 100, 94, True, "trim"),  # within 2% runtime, 6% fewer lines
        (1.0, 1.01, True, 100, 97, False, "below"),  # not enough trimmed
        (1.0, 1.05, True, 100, 50, False, "below"),  # slower than the trim band
    ],
)
def test_accept_table(before, after, tests_ok, sloc_b, sloc_a, ok, why) -> None:
    accepted, reason = accept(before, after, tests_ok, sloc_b, sloc_a, CFG.optimizer_min_gain)
    assert accepted is ok
    assert why in reason


def test_gain() -> None:
    assert gain(2.0, 1.5) == pytest.approx(0.25)
    assert gain(1.0, 1.2) == pytest.approx(-0.2)


def test_decide_table() -> None:
    assert decide([], CFG, target_s=None, baseline_s=1.0) == ("continue", "first attempt")
    # target met by the baseline already
    assert decide([], CFG, target_s=2.0, baseline_s=1.0)[0] == "stop"
    good = [_outcome(1, 1.0, 0.4, True)]
    action, reason = decide(good, CFG, target_s=0.5, baseline_s=1.0)
    assert action == "stop" and "target" in reason
    # patience: two consecutive sub-threshold attempts
    meh = [_outcome(1, 1.0, 0.99, False), _outcome(2, 1.0, None, False)]
    action, reason = decide(meh, CFG, target_s=None, baseline_s=1.0)
    assert action == "stop" and "patience" in reason
    # max attempts
    four = [_outcome(i, 1.0, 0.8**i, True) for i in range(1, 5)]
    action, reason = decide(four, CFG, target_s=None, baseline_s=1.0)
    assert action == "stop" and "max attempts" in reason
    # budget
    assert decide(good, CFG, None, 1.0, budget_ok=False) == ("stop", "budget exhausted")


@given(st.lists(st.floats(min_value=-0.5, max_value=0.6), min_size=1, max_size=30))
def test_loop_properties(gains: list[float]) -> None:
    """Simulate the loop: never exceeds max attempts; stops as soon as patience runs out."""
    outcomes: list[OptimizationOutcome] = []
    runtime = 1.0
    below_streak = 0
    for i, g in enumerate(gains, start=1):
        action, _ = decide(outcomes, CFG, target_s=None, baseline_s=1.0)
        if action == "stop":
            break
        after = runtime * (1 - g)
        ok, _ = accept(runtime, after, True, 100, 100, CFG.optimizer_min_gain)
        outcomes.append(_outcome(i, runtime, after, ok))
        below_streak = 0 if g >= CFG.optimizer_min_gain else below_streak + 1
        if ok:
            runtime = after
        assert below_streak <= CFG.optimizer_patience
    assert len(outcomes) <= CFG.optimizer_max_attempts
