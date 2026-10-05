"""study-3: M2's spec kept overflowing the spec limits and crashed the run. Now a spec that still
does not fit after its repair asks the long planner to split the milestone, once per milestone;
a second overflow stops the run with a clear message."""

from pathlib import Path

import pytest
from graph_helpers import (
    PASS,
    ROADMAP,
    SPEC,
    FakeChannel,
    coder_pass,
    decide,
    design,
    make_deps,
)

from r2r.graph.driver import drive, open_graph
from r2r.llm.fake import FakeLLM
from r2r.llm.gateway import ContractViolation

TOO_LONG = (
    '{"tasks": ["'
    + "x" * 5000
    + '"], "acceptance_criteria": [{"id": "AC1", "text": "c"}], "rationale": "r"}'
)


def _payload(fake: FakeLLM, role: str, i: int) -> str:
    return str([c for c in fake.calls if c.role == role][i].messages[1]["content"])


def test_oversized_spec_asks_the_long_planner_to_split(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP, ROADMAP)
    fake.script("short_planner", TOO_LONG, TOO_LONG, SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", design(1))
    fake.script("coder", *coder_pass(1))
    deps = make_deps(tmp_path, fake)
    ch = FakeChannel(decide("approve"), decide("stop"))  # roadmap, then the result gate
    with open_graph(deps) as graph:
        drive(graph, deps.run_id, ch, start=True)
    note = _payload(fake, "long_planner", 1)
    assert "could not fit" in note and "split M1" in note
    assert [c.role for c in fake.calls].count("coordinator") == 1  # the replanned spec went on


def test_second_overflow_on_the_same_milestone_stops_clearly(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP, ROADMAP)
    fake.script("short_planner", TOO_LONG, TOO_LONG, TOO_LONG, TOO_LONG)
    deps = make_deps(tmp_path, fake)
    with open_graph(deps) as graph, pytest.raises(ContractViolation, match="split already tried"):
        drive(graph, deps.run_id, FakeChannel(decide("approve")), start=True)
