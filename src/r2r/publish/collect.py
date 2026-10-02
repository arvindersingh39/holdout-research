"""Gather a run's finished results from its checkpoint history (Phase 8).

Graph state holds only the current milestone's digest and MathSpec, so each milestone's newest
values are recovered from the history of states. Works for runs started before Phase 8.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from r2r.contracts import (
    BaselineCard,
    MathSpec,
    Milestone,
    ResearchBrief,
    ResultDigest,
    Roadmap,
)


@dataclass(frozen=True)
class MilestoneRecord:
    milestone: Milestone
    digest: ResultDigest
    math_spec: MathSpec | None
    baselines: list[BaselineCard] = field(default_factory=list)


@dataclass(frozen=True)
class RunRecord:
    roadmap: Roadmap
    brief: ResearchBrief | None
    milestones: list[MilestoneRecord]
    title: str = ""  # the proposal's title

    @property
    def partial(self) -> bool:
        """True when some roadmap milestone isn't done (the paper says so)."""
        return any(m.status != "done" for m in self.roadmap.milestones)


class NothingToPublish(ValueError):
    pass


def collect_from_states(states: Iterable[Mapping[str, Any]]) -> RunRecord:
    """`states` newest first, as `graph.get_state_history` yields them."""
    roadmap: Roadmap | None = None
    brief: ResearchBrief | None = None
    title = ""
    found: dict[str, tuple[ResultDigest, MathSpec | None, list[BaselineCard]]] = {}
    for s in states:
        roadmap = roadmap or s.get("roadmap")
        brief = brief or s.get("brief")
        title = title or (s["proposal"].title if s.get("proposal") is not None else "")
        d = s.get("digest")
        if d is None or d.milestone_id in found:
            continue
        ms = s.get("math_spec")
        found[d.milestone_id] = (
            d,
            ms if ms is not None and ms.milestone_id == d.milestone_id else None,
            [b for b in s.get("baselines") or [] if b.milestone_id == d.milestone_id],
        )
    if roadmap is None or not found:
        raise NothingToPublish("no finished milestone in this run yet")
    records = [MilestoneRecord(m, *found[m.id]) for m in roadmap.milestones if m.id in found]
    return RunRecord(roadmap=roadmap, brief=brief, milestones=records, title=title)


def collect_run(graph: Any, run_id: str) -> RunRecord:
    from r2r.graph.driver import config

    return collect_from_states(snap.values for snap in graph.get_state_history(config(run_id)))
