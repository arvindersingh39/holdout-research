"""Simplifier stop rule (D85): keep or revert each attempt, and when to stop the loop.

Pure functions. Every keep/revert/stop decision is made here, never by an LLM. The objective is
the simplest code with the same maths: a patch is kept only if the frozen tests, checks and sanity
checks still pass, complexity falls by at least `simplify_min_cut`, and the benchmark is at most
`simplify_runtime_guard` times slower.
"""

from __future__ import annotations

from typing import Literal

from r2r.contracts import OptimizationOutcome
from r2r.settings import BudgetsConfig

Action = Literal["continue", "stop"]


def accept(
    before_s: float,
    after_s: float,
    tests_ok: bool,
    complexity_before: int,
    complexity_after: int,
    cfg: BudgetsConfig,
) -> tuple[bool, str]:
    if not tests_ok:
        return False, "rejected: tests or checks failed"
    if after_s > before_s * cfg.simplify_runtime_guard:
        return False, f"rejected: slower ({before_s:.3g} s → {after_s:.3g} s)"
    cut = (complexity_before - complexity_after) / max(complexity_before, 1)
    if cut + 1e-12 >= cfg.simplify_min_cut:
        return True, (
            f"accepted: simpler, complexity {complexity_before} → {complexity_after} ({cut:.0%})"
        )
    return False, f"rejected: complexity cut {cut:.1%} below {cfg.simplify_min_cut:.0%}"


def decide(
    outcomes: list[OptimizationOutcome], cfg: BudgetsConfig, budget_ok: bool = True
) -> tuple[Action, str]:
    """Whether to try another simplification, with the reason logged either way."""
    if not budget_ok:
        return "stop", "budget exhausted"
    if len(outcomes) >= cfg.optimizer_max_attempts:
        return "stop", f"max attempts reached ({cfg.optimizer_max_attempts})"
    p = cfg.optimizer_patience
    if len(outcomes) >= p and not any(o.accepted for o in outcomes[-p:]):
        return "stop", f"patience: {p} rejected attempts in a row"
    return "continue", "first attempt" if not outcomes else "simplifications still possible"
