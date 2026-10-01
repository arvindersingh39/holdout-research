"""Optimizer stop rule: keep or revert each attempt, and when to stop the loop (Phase 4).

Pure functions. Every keep/revert/stop decision is made here, never by an LLM.
"""

from __future__ import annotations

from typing import Literal

from r2r.contracts import OptimizationOutcome
from r2r.settings import BudgetsConfig

TRIM_RUNTIME_BAND = 0.02  # a "trim" may change runtime by at most ±2%
TRIM_MIN_SLOC_CUT = 0.05  # ... and must cut at least 5% of source lines

Action = Literal["continue", "stop"]


def gain(before_s: float, after_s: float) -> float:
    """Relative runtime improvement: 0.25 means 25% faster."""
    return (before_s - after_s) / before_s


def accept(
    before_s: float,
    after_s: float,
    tests_ok: bool,
    sloc_before: int,
    sloc_after: int,
    min_gain: float,
) -> tuple[bool, str]:
    if not tests_ok:
        return False, "rejected: tests or checks failed"
    g = gain(before_s, after_s)
    if g >= min_gain:
        return True, f"accepted: {g:.1%} faster"
    if abs(g) <= TRIM_RUNTIME_BAND and sloc_after <= sloc_before * (1 - TRIM_MIN_SLOC_CUT):
        return True, f"accepted as trim: runtime {g:+.1%}, SLOC {sloc_before} → {sloc_after}"
    return False, f"rejected: gain {g:.1%} below {min_gain:.0%}"


def _sub_threshold(o: OptimizationOutcome, min_gain: float) -> bool:
    if o.runtime_after_s is None:
        return True
    return gain(o.runtime_before_s, o.runtime_after_s) < min_gain


def decide(
    outcomes: list[OptimizationOutcome],
    cfg: BudgetsConfig,
    target_s: float | None,
    baseline_s: float,
    budget_ok: bool = True,
) -> tuple[Action, str]:
    """Whether to try another optimisation attempt, with the reason logged either way."""
    if not budget_ok:
        return "stop", "budget exhausted"
    accepted = [o for o in outcomes if o.accepted and o.runtime_after_s is not None]
    current = accepted[-1].runtime_after_s if accepted else baseline_s
    if target_s is not None and current is not None and current <= target_s:
        return "stop", f"target met: {current:.3f} s <= {target_s:.3f} s"
    if len(outcomes) >= cfg.optimizer_max_attempts:
        return "stop", f"max attempts reached ({cfg.optimizer_max_attempts})"
    p = cfg.optimizer_patience
    if len(outcomes) >= p and all(_sub_threshold(o, cfg.optimizer_min_gain) for o in outcomes[-p:]):
        return "stop", f"patience: {p} attempts in a row below {cfg.optimizer_min_gain:.0%}"
    return "continue", "first attempt" if not outcomes else "gains still possible"
