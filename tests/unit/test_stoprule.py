"""Simplifier stop rule (D85): keep/revert per attempt and when to stop. Pure functions.

A patch is kept only if the tests, checks and sanity checks pass, the code is simpler by at
least `simplify_min_cut`, and it is at most `simplify_runtime_guard` times slower.
"""

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from r2r.contracts import OptimizationOutcome
from r2r.harness.stoprule import accept, decide
from r2r.settings import BudgetsConfig, load_settings

REPO = Path(__file__).resolve().parents[2]
# simplify_min_cut 3%, runtime guard 1.10, patience 2, max attempts 4
CFG: BudgetsConfig = load_settings(REPO / "tests" / "fixtures" / "config", env_file=None).budgets


def _outcome(n: int, accepted: bool) -> OptimizationOutcome:
    return OptimizationOutcome(
        created_by="harness",
        run_id="r",
        milestone_id="M1",
        attempt=n,
        accepted=accepted,
        reason="x",
        runtime_before_s=1.0,
        runtime_after_s=1.0,
        sloc_before=100,
        sloc_after=100,
        complexity_before=100,
        complexity_after=90 if accepted else 100,
    )


@pytest.mark.parametrize(
    ("before", "after", "tests_ok", "cx_b", "cx_a", "ok", "why"),
    [
        (1.0, 1.00, True, 100, 90, True, "simpler"),
        (1.0, 1.00, True, 100, 97, True, "simpler"),  # exactly the 3% cut
        (1.0, 1.00, True, 100, 98, False, "below"),
        (1.0, 0.50, True, 100, 100, False, "below"),  # faster alone is not the objective
        (1.0, 1.09, True, 100, 80, True, "simpler"),  # within the 10% runtime guard
        (1.0, 1.20, True, 100, 50, False, "slower"),
        (1.0, 0.50, False, 100, 50, False, "tests"),
    ],
)
def test_accept_table(before, after, tests_ok, cx_b, cx_a, ok, why) -> None:
    accepted, reason = accept(before, after, tests_ok, cx_b, cx_a, CFG)
    assert accepted is ok
    assert why in reason


def test_decide_table() -> None:
    assert decide([], CFG) == ("continue", "first attempt")
    action, reason = decide([_outcome(1, False), _outcome(2, False)], CFG)
    assert action == "stop" and "patience" in reason
    assert decide([_outcome(1, False), _outcome(2, True)], CFG)[0] == "continue"
    four = [_outcome(i, True) for i in range(1, 5)]
    action, reason = decide(four, CFG)
    assert action == "stop" and "max attempts" in reason
    assert decide([_outcome(1, True)], CFG, budget_ok=False) == ("stop", "budget exhausted")


@given(st.lists(st.booleans(), min_size=1, max_size=30))
def test_loop_properties(kept: list[bool]) -> None:
    """Never exceeds max attempts; stops as soon as `patience` rejections come in a row."""
    outcomes: list[OptimizationOutcome] = []
    streak = 0
    for i, k in enumerate(kept, start=1):
        if decide(outcomes, CFG)[0] == "stop":
            break
        outcomes.append(_outcome(i, k))
        streak = 0 if k else streak + 1
        assert streak <= CFG.optimizer_patience
    assert len(outcomes) <= CFG.optimizer_max_attempts
