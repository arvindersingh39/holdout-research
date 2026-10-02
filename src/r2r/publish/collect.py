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
    digests: dict[str, ResultDigest] = {}
    specs: dict[str, MathSpec] = {}
    cards: dict[str, list[BaselineCard]] = {}
    for s in states:  # newest first: the first value seen for a milestone wins
        roadmap = roadmap or s.get("roadmap")
        brief = brief or s.get("brief")
        title = title or (s["proposal"].title if s.get("proposal") is not None else "")
        if (d := s.get("digest")) is not None:
            digests.setdefault(d.milestone_id, d)
        if (ms := s.get("math_spec")) is not None:  # reset to None once a result is approved
            specs.setdefault(ms.milestone_id, ms)
        for b in s.get("baselines") or []:
            if b.milestone_id not in cards:
                cards[b.milestone_id] = [
                    x for x in s["baselines"] if x.milestone_id == b.milestone_id
                ]
    if roadmap is None or not digests:
        raise NothingToPublish("no finished milestone in this run yet")
    records = [
        MilestoneRecord(m, digests[m.id], specs.get(m.id), cards.get(m.id, []))
        for m in roadmap.milestones
        if m.id in digests
    ]
    return RunRecord(roadmap=roadmap, brief=brief, milestones=records, title=title)


def collect_run(graph: Any, run_id: str) -> RunRecord:
    from r2r.graph.driver import config

    return collect_from_states(snap.values for snap in graph.get_state_history(config(run_id)))
