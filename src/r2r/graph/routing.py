"""Loop decisions as plain Python: never an LLM call (CLAUDE.md architecture rules)."""

from __future__ import annotations

from typing import Literal

from r2r.contracts import CoordinatorVerdict, HumanDecision, Issue, Milestone, ResultDigest, Roadmap

END = "__end__"
GateKind = Literal[
    "roadmap",
    "escalation",
    "tests",
    "design",
    "coder",
    "tamper",
    "budget",
    "dispute",
    "result",
    "publication",
]
_FINISHED = {"done", "skipped"}


def next_milestone(roadmap: Roadmap) -> Milestone | None:
    """The current milestone if it's still open, else the first open one whose deps are done."""
    by_id = {m.id: m for m in roadmap.milestones}
    current = by_id.get(roadmap.current_milestone_id or "")
    if current and current.status not in _FINISHED and _deps_done(current, by_id):
        return current
    for m in roadmap.milestones:
        if m.status not in _FINISHED and _deps_done(m, by_id):
            return m
    return None


def _deps_done(m: Milestone, by_id: dict[str, Milestone]) -> bool:
    return all(by_id[d].status in _FINISHED for d in m.depends_on if d in by_id)


def mark_done(roadmap: Roadmap, milestone_id: str) -> Roadmap:
    data = roadmap.model_dump()
    for m in data["milestones"]:
        if m["id"] == milestone_id:
            m["status"] = "done"
    return Roadmap.model_validate(data)


def current_on_open(roadmap: Roadmap) -> Roadmap:
    """Point `current_milestone_id` at the next open milestone if it names a finished one."""
    by_id = {m.id: m for m in roadmap.milestones}
    cur = by_id.get(roadmap.current_milestone_id or "")
    nxt = next_milestone(roadmap)
    if cur is not None and cur.status in _FINISHED and nxt is not None:
        return roadmap.model_copy(update={"current_milestone_id": nxt.id})
    return roadmap


def advance_without_replan(
    roadmap: Roadmap | None,
    digest: ResultDigest | None,
    note: str | None,
    issues: list[Issue],
) -> Roadmap | None:
    """After an approved, fully met result with no note and no roadmap issues, the plan stands:
    the next open milestone starts without a long-planner call (leak 3). None means replan."""
    if roadmap is None or digest is None or note or issues or digest.status != "met":
        return None
    by_id = {m.id: m for m in roadmap.milestones}
    done = by_id.get(digest.milestone_id)
    if roadmap.current_milestone_id != digest.milestone_id or done is None:
        return None
    if done.status != "done":
        return None
    nxt = next_milestone(roadmap)
    return roadmap.model_copy(update={"current_milestone_id": nxt.id if nxt else done.id})


def keep_finished(old: Roadmap | None, new: Roadmap) -> Roadmap:
    """Code owns milestone status: anything finished before stays finished."""
    if old is None:
        return new
    finished = {m.id: m.status for m in old.milestones if m.status in _FINISHED}
    data = new.model_dump()
    for m in data["milestones"]:
        if m["id"] in finished:
            m["status"] = finished[m["id"]]
    return Roadmap.model_validate(data)


def is_material_change(old: Roadmap | None, new: Roadmap) -> bool:
    """Milestones added, removed or reordered, or the goal changed."""
    if old is None:
        return True
    return old.goal != new.goal or [m.id for m in old.milestones] != [m.id for m in new.milestones]


def route_after_coordinator(verdict: CoordinatorVerdict, rounds: int, max_rounds: int) -> str:
    """`rounds` counts revise verdicts so far for this milestone, including this one."""
    if verdict.verdict == "pass":
        return "scout"
    if rounds > max_rounds:
        return "escalation_gate"
    if any(i.target == "roadmap" for i in verdict.issues):
        return "long_plan"
    return "short_plan"


def route_after_gate(gate: GateKind, decision: HumanDecision) -> str:
    if decision.action == "stop":
        return END
    if decision.action == "redirect":
        return "design" if gate == "tests" else "long_plan"
    return {
        "roadmap": "short_plan",
        "escalation": "scout",
        "dispute": "revise_test",
        "tests": "freeze",
        "design": "freeze",
        "coder": "digest",
        "tamper": "code",
        "result": "long_plan",
        "publication": END,
    }[gate]


def needs_test_review(review_tests: str, roadmap: Roadmap) -> bool:
    if review_tests == "always":
        return True
    if review_tests == "never":
        return False
    return not any(m.status == "done" for m in roadmap.milestones)  # first_milestone


def route_after_design(redgreen_ok: bool, failed_attempts: int, review: bool) -> str:
    """One automatic redesign; a second failure goes to the human design gate."""
    if redgreen_ok:
        return "tests_gate" if review else "freeze"
    return "design" if failed_attempts < 2 else "design_gate"


def route_after_code(
    tampered: bool, passed: bool, sessions: int, max_sessions: int, disputed: bool = False
) -> str:
    if tampered:
        return "tamper_gate"
    if passed:
        return "opt_baseline"
    if disputed:
        return "dispute_gate"
    return "code" if sessions < max_sessions else "coder_gate"


def route_after_optimize(tampered: bool, stop_reason: str | None) -> str:
    if tampered:
        return "tamper_gate"
    return "digest" if stop_reason else "optimize"
