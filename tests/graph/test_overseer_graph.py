"""Phase 7c in the graph: overseer hook points, briefings in the agents' prompts, flags at gates."""

import json
from pathlib import Path

import pytest
from graph_helpers import (
    PASS,
    ROADMAP,
    SPEC,
    FakeChannel,
    decide,
    design,
    make_deps,
    script_milestone,
)

import r2r.graph.nodes as nodes
from r2r.graph.driver import drive, open_graph
from r2r.llm.fake import FakeLLM
from r2r.llm.gateway import BudgetExceeded


def rep(point: str, briefings=(), flags=()) -> str:
    return json.dumps({"point": point, "briefings": list(briefings), "flags": list(flags)})


def brief(role: str, text: str) -> dict:
    return {"role": role, "text": text, "evidence": ["E1"]}


FLAG = {
    "severity": "high",
    "text": "AC1's expected value contradicts its formula",
    "evidence": ["E1"],
}


def _run(tmp_path: Path, fake: FakeLLM, ch: FakeChannel, **kw):
    deps = make_deps(tmp_path, fake, overseer=True, **kw)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    return deps, res


def _system(call) -> list:
    return call.messages[0]["content"]


def _calls(fake: FakeLLM, role: str) -> list:
    return [c for c in fake.calls if c.role == role]


def _points(fake: FakeLLM) -> list[str]:
    return [
        json.loads(r.messages[1]["content"].split("<data>")[1].split("</data>")[0])["point"]
        for r in _calls(fake, "overseer")
    ]


def test_hooks_briefings_and_flags_through_one_milestone(tmp_path: Path, fake: FakeLLM) -> None:
    """D93: plan, then the design before coding (its briefing reaches the coder), then result."""
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    fake.script(
        "overseer",
        rep("plan", flags=[FLAG]),
        rep("design", [brief("coder", "Use float64 throughout square().")]),
        rep("result", [brief("short_planner", "Next spec: state tolerances explicitly.")]),
    )
    ch = FakeChannel(decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "result"]
    assert _points(fake) == ["plan", "design", "result"]
    assert "Overseer" in ch.requests[0].markdown and FLAG["text"] in ch.requests[0].markdown
    coder = _calls(fake, "coder")[0]
    assert "Use float64 throughout" in _system(coder)[1]["text"]
    design_call = _calls(fake, "overseer")[1].messages[1]["content"]
    assert "code_file" in design_call and "raise NotImplementedError" in design_call  # the stubs
    result_md = ch.requests[-1].markdown  # gates name briefed agents; the text is in `show`
    assert "Briefed: short planner" in result_md and "Next spec: state tolerances" not in result_md
    assert FLAG["text"] not in result_md  # roadmap flags were settled at the roadmap gate
    assert res.state["digest"].status == "met"


def test_stuck_coder_gets_the_overseers_read_at_the_coder_gate(
    tmp_path: Path, fake: FakeLLM
) -> None:
    stuck = {"severity": "warn", "text": "The coder never calls a tool.", "evidence": ["E1"]}
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", design(1))
    fake.script("coder", *"abcdef")
    fake.script("overseer", rep("plan"), rep("design"), rep("code", flags=[stuck]))
    ch = FakeChannel(decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "coder"] and _points(fake) == ["plan", "design", "code"]
    assert stuck["text"] in ch.requests[-1].markdown


def test_planner_briefing_carries_to_next_milestone_others_cleared(
    tmp_path: Path, fake: FakeLLM
) -> None:
    fake.script("long_planner", ROADMAP, ROADMAP)
    script_milestone(fake, 1)
    script_milestone(fake, 2)
    fake.script(
        "overseer",
        rep("plan"),
        rep("design"),
        rep(
            "result",
            [brief("short_planner", "CARRY-ME"), brief("math_designer", "M1-ONLY")],
        ),
        rep("design"),
        rep("result"),
    )
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    sp = _calls(fake, "short_planner")
    assert len(_system(sp[0])) == 1 and "CARRY-ME" in _system(sp[1])[1]["text"]
    assert all("M1-ONLY" not in str(_system(c)) for c in _calls(fake, "math_designer"))


def test_call_cap_skips_hooks(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    fake.script("overseer", rep("plan"))
    _run(tmp_path, fake, FakeChannel(decide("approve"), decide("stop")), overseer_max_calls=1)
    assert len(_calls(fake, "overseer")) == 1


def test_disabled_overseer_makes_no_calls(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    deps = make_deps(tmp_path, fake)  # disabled by default in graph tests
    with open_graph(deps) as graph:
        drive(
            graph,
            deps.run_id,
            FakeChannel(decide("approve"), decide("stop")),
            start=True,
        )
    assert _calls(fake, "overseer") == []


def test_overseer_budget_exceeded_goes_to_budget_gate(
    tmp_path: Path, fake: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broke(*a, **k):
        raise BudgetExceeded("run cap reached")

    monkeypatch.setattr(nodes, "run_overseer", broke)
    fake.script("long_planner", ROADMAP)
    ch = FakeChannel(decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["budget"] and res.status == "stopped"


def test_roadmap_redirect_clears_flags_on_the_old_roadmap(tmp_path: Path, fake: FakeLLM) -> None:
    """study-3 extension: a flag on roadmap v30 was still shown at the v31 gate after it was fixed."""
    fake.script("long_planner", ROADMAP, ROADMAP)
    fake.script(
        "overseer",
        rep("plan", [brief("long_planner", "Recompute AC1's value.")], [FLAG]),
        rep("plan"),
    )
    ch = FakeChannel(decide("redirect", note="Fix AC1's value."), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "roadmap"]
    assert FLAG["text"] in ch.requests[0].markdown
    assert FLAG["text"] not in ch.requests[1].markdown  # the redone roadmap starts clean
    second = _calls(fake, "long_planner")[1]
    assert "Recompute AC1's value." in _system(second)[1]["text"]  # the advice still reaches it
