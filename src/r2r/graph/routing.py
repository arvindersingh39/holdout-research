"""Loop decisions as plain Python: never an LLM call (CLAUDE.md architecture rules)."""

from __future__ import annotations

from typing import Literal

from r2r.contracts import CoordinatorVerdict, HumanDecision, Issue, Milestone, ResultDigest, Roadmap

END = "__end__"
GateKind = Literal["roadmap", "coder", "benchmark", "budget", "result", "publication"]
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
    """Milestones added, removed or reordered, the goal changed, or benchmarks reassigned (D93:
    the owner confirms which values each milestone must reproduce)."""
    if old is None:
        return True
    if old.goal != new.goal or [m.id for m in old.milestones] != [m.id for m in new.milestones]:
        return True
    return [m.benchmarks for m in old.milestones] != [m.benchmarks for m in new.milestones]


def route_after_coordinator(verdict: CoordinatorVerdict, rounds: int, max_rounds: int) -> str:
    """`rounds` counts revise verdicts so far for this milestone, including this one. Past the
    limit the spec goes to the designer with the open issues as notes (D93: no escalation gate)."""
    if verdict.verdict == "pass" or rounds > max_rounds:
        return "design"
    if any(i.target == "roadmap" for i in verdict.issues):
        return "long_plan"
    return "short_plan"


def route_after_gate(gate: GateKind, decision: HumanDecision) -> str:
    if decision.action == "stop":
        return END
    if decision.action == "redirect":
        return "long_plan"
    return {
        "roadmap": "short_plan",
        "coder": "digest",
        "benchmark": "compare",
        "result": "long_plan",
        "publication": END,
    }[gate]


def route_after_code(passed: bool, sessions: int, max_sessions: int, has_benchmarks: bool) -> str:
    if passed:
        return "benchmark_gate" if has_benchmarks else "digest"
    return "code" if sessions < max_sessions else "coder_gate"


def route_after_compare(status: str | None, retry: bool) -> str:
    """The owner's results file: unusable → ask again; wrong values → the coder (bounded by
    `max_fix_rounds`); otherwise the digest reports the comparison."""
    if status is None:
        return "benchmark_gate"
    return "code" if retry else "digest"
