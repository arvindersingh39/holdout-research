"""Phase 7c in the graph: overseer hook points, briefings in the agents' prompts, flags at gates."""

import json
from pathlib import Path

import pytest
from graph_helpers import (
    PASS,
    ROADMAP,
    SPEC,
    VACUOUS_TESTS,
    FakeChannel,
    coder_pass,
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


def test_hooks_briefings_and_flags_through_one_milestone(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", design(1, VACUOUS_TESTS), design(1))
    fake.script("coder", *coder_pass(1))
    fake.script(
        "overseer",
        rep("plan", flags=[FLAG]),
        rep("design", [brief("math_designer", "Each test must call the stub; no assert True.")]),
        rep("result", [brief("short_planner", "Next spec: state tolerances explicitly.")]),
    )
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "tests", "result"]
    assert [
        json.loads(r.messages[1]["content"].split("<data>")[1].split("</data>")[0])["point"]
        for r in _calls(fake, "overseer")
    ] == ["plan", "design", "result"]  # no spec hook (D86)
    assert "Overseer" in ch.requests[0].markdown and FLAG["text"] in ch.requests[0].markdown
    first, second = _calls(fake, "math_designer")
    assert len(_system(first)) == 1
    assert "no assert True" in _system(second)[1]["text"]
    result_md = ch.requests[-1].markdown  # gates name briefed agents; the text is in `show`
    assert "Briefed: short planner" in result_md and "Next spec: state tolerances" not in result_md
    assert "no assert True" not in ch.requests[-1].markdown  # design-stage briefing dropped
    assert res.state["digest"].status == "met"


def test_failed_coder_session_gets_a_briefing(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", design(1))
    fake.script("coder", "I give up", *coder_pass(1))
    fake.script(
        "overseer",
        rep("plan"),
        rep("code", [brief("coder", "square(x) is x*x; edit src/sq1/core.py.")]),
        rep("result"),
    )
    _run(tmp_path, fake, FakeChannel(decide("approve"), decide("approve"), decide("stop")))
    sessions = _calls(fake, "coder")
    assert len(_system(sessions[0])) == 1
    assert "edit src/sq1/core.py" in _system(sessions[1])[1]["text"]


def test_planner_briefing_carries_to_next_milestone_others_cleared(
    tmp_path: Path, fake: FakeLLM
) -> None:
    fake.script("long_planner", ROADMAP, ROADMAP)
    script_milestone(fake, 1)
    script_milestone(fake, 2)
    fake.script(
        "overseer",
        rep("plan"),
        rep(
            "result",
            [brief("short_planner", "CARRY-ME"), brief("math_designer", "M1-ONLY")],
        ),
        rep("result"),
    )
    ch = FakeChannel(decide("approve"), decide("approve"), decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    sp = _calls(fake, "short_planner")
    assert len(_system(sp[0])) == 1 and "CARRY-ME" in _system(sp[1])[1]["text"]
    assert all("M1-ONLY" not in str(_system(c)) for c in _calls(fake, "math_designer"))


def test_call_cap_skips_hooks(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    fake.script("overseer", rep("plan"))
    _run(
        tmp_path,
        fake,
        FakeChannel(decide("approve"), decide("approve"), decide("stop")),
        overseer_max_calls=1,
    )
    assert len(_calls(fake, "overseer")) == 1


def test_disabled_overseer_makes_no_calls(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    deps = make_deps(tmp_path, fake)  # disabled by default in graph tests
    with open_graph(deps) as graph:
        drive(
            graph,
            deps.run_id,
            FakeChannel(decide("approve"), decide("approve"), decide("stop")),
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


def test_design_gate_gets_the_overseers_read(tmp_path: Path, fake: FakeLLM) -> None:
    """After a second failed design the owner decides; the overseer reviews first."""
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", design(1, VACUOUS_TESTS), design(1, VACUOUS_TESTS))
    gate_flag = {
        "severity": "high",
        "text": "The tests are fine; the reference is wrong.",
        "evidence": ["E1"],
    }
    fake.script(
        "overseer",
        rep("plan"),
        rep("design"),
        rep("design_gate", flags=[gate_flag]),
    )
    ch = FakeChannel(decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "design"]
    points = [
        json.loads(r.messages[1]["content"].split("<data>")[1].split("</data>")[0])["point"]
        for r in _calls(fake, "overseer")
    ]
    assert points == ["plan", "design", "design_gate"]
    assert gate_flag["text"] in ch.requests[-1].markdown


def test_redirect_clears_stale_overseer_advice(tmp_path: Path, fake: FakeLLM) -> None:
    """study-3: briefings and flags from before a redirect kept steering the redesign."""
    stale_flag = {"severity": "high", "text": "STALE-FLAG about an old design", "evidence": ["E1"]}
    fake.script("long_planner", ROADMAP, ROADMAP)
    fake.script("short_planner", SPEC, SPEC)
    fake.script("coordinator", PASS, PASS)
    fake.script("math_designer", design(1, VACUOUS_TESTS), design(1, VACUOUS_TESTS), design(1))
    fake.script(
        "overseer",
        rep("plan"),
        rep(
            "design",
            [brief("math_designer", "STALE-ADVICE: write stub-injection audits")],
            [stale_flag],
        ),
        rep("design_gate"),
    )
    ch = FakeChannel(decide("approve"), decide("redirect", note="Simplify M1."), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "design", "tests"]
    third = _calls(fake, "math_designer")[2]
    assert "STALE-ADVICE" not in str(third.messages)
    assert "STALE-FLAG" not in ch.requests[-1].markdown
