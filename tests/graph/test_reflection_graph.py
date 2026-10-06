"""D94 in the graph: the overseer critiques each new roadmap and each design once; the author
revises once, answering every item; the owner sees the review at the gate."""

import json
from pathlib import Path

import pytest
from graph_helpers import (
    BRIEF,
    IMPL,
    PASS,
    ROADMAP,
    SPEC,
    STUB,
    FakeChannel,
    coder_pass,
    decide,
    design,
    make_deps,
    script_milestone,
)

import r2r.graph.nodes as nodes
from r2r.graph.driver import drive, open_graph
from r2r.harness.gitops import Git
from r2r.llm.fake import FakeLLM
from r2r.llm.gateway import BudgetExceeded

META = {"created_by", "run_id", "milestone_id"}


def critique(point: str, *items) -> str:
    return json.dumps({"point": point, "items": list(items)})


def item(i: int, text: str, severity: str = "high") -> dict:
    return {"id": f"C{i}", "severity": severity, "text": text, "evidence": ["E1"]}


def result_report() -> str:
    return json.dumps({"point": "result", "briefings": [], "flags": []})


def roadmap_revision(goal: str, *answers) -> str:
    rm = ROADMAP.model_copy(update={"goal": goal}).model_dump(exclude=META)
    return json.dumps(
        {"roadmap": rm, "responses": [{"id": i, "verdict": v, "text": t} for i, v, t in answers]}
    )


def design_revision(n: int, stub: str, *answers) -> str:
    d = design(n).model_dump(exclude=META)
    d["spec"] = {k: v for k, v in d["spec"].items() if k not in META}
    for f in d["files"]:
        if f["path"].endswith("core.py"):
            f["content"] = stub
    return json.dumps(
        {"design": d, "responses": [{"id": i, "verdict": v, "text": t} for i, v, t in answers]}
    )


def _run(tmp_path: Path, fake: FakeLLM, ch: FakeChannel, **kw):
    deps = make_deps(tmp_path, fake, overseer=True, reflect=True, **kw)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    return deps, res


def _calls(fake: FakeLLM, role: str) -> list:
    return [c for c in fake.calls if c.role == role]


def test_roadmap_critique_revision_and_review_at_the_gate(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script(
        "long_planner",
        ROADMAP,
        roadmap_revision(
            "Revised goal.",
            ("C1", "changed", "M1 now states the tolerance"),
            ("C2", "rebutted", "the proposal fixes T"),
        ),
    )
    fake.script(
        "overseer",
        critique("plan", item(1, "AC1 has no tolerance"), item(2, "T should be 365")),
    )
    ch = FakeChannel(decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap"] and res.state["roadmap"].goal == "Revised goal."
    assert res.state["roadmap"].version == 1  # one cycle: the revision keeps the draft's version
    md = ch.requests[0].markdown
    assert "Roadmap review" in md and "C1 (high): AC1 has no tolerance" in md
    assert "changed: M1 now states the tolerance" in md
    assert "rebutted: the proposal fixes T" in md
    revision = str(_calls(fake, "long_planner")[1].messages[1]["content"])
    assert "AC1 has no tolerance" in revision and "Price European options" in revision
    assert res.state["review_flags"] == []  # settled by the owner's decision at the gate


def test_no_items_means_no_revision_and_failures_keep_the_draft(
    tmp_path: Path, fake: FakeLLM
) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("overseer", critique("plan"))  # sound draft: nothing to revise
    ch = FakeChannel(decide("stop"))
    _run(tmp_path, fake, ch)
    assert (
        len(_calls(fake, "long_planner")) == 1 and "Roadmap review" not in ch.requests[0].markdown
    )

    other = FakeLLM()
    other.script("retriever", BRIEF)
    other.script("long_planner", ROADMAP, "bad", "bad", "bad")  # revision unusable after repairs
    other.script("overseer", critique("plan", item(1, "split M1")))
    ch = FakeChannel(decide("stop"))
    (tmp_path / "b").mkdir()
    _, res = _run(tmp_path / "b", other, ch)
    md = ch.requests[0].markdown
    assert "revision failed" in md and "C1 (high): split M1" in md
    assert res.state["roadmap"].goal == ROADMAP.goal  # the valid draft went on


def test_design_critique_revised_stubs_are_written_and_coded(tmp_path: Path, fake: FakeLLM) -> None:
    revised_stub = STUB.replace(
        "raise NotImplementedError", '"""x squared."""\n    raise NotImplementedError'
    )
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script(
        "math_designer",
        design(1),
        design_revision(1, revised_stub, ("C1", "changed", "documented the return value")),
    )
    fake.script("coder", *coder_pass(1))
    fake.script(
        "overseer",
        critique("plan"),
        critique("design", item(1, "square() needs a docstring", "warn")),
        result_report(),
    )
    ch = FakeChannel(decide("approve"), decide("stop"))
    deps, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "result"]
    committed = Git(deps.workspace()).run("show", "HEAD~1:src/sq1/core.py")  # the design
    assert '"""x squared."""' in committed  # the revision, not the draft, was written
    assert (deps.workspace().root / "src/sq1/core.py").read_text() == IMPL
    md = ch.requests[-1].markdown
    assert "Design review" in md and "changed: documented the return value" in md
    critique_call = _calls(fake, "overseer")[1]
    assert "raise NotImplementedError" in str(
        critique_call.messages[1]["content"]
    )  # read the stubs
    assert res.state["digest"].status == "met"


def test_shortcut_milestones_get_no_roadmap_critique(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    script_milestone(fake, 2)
    fake.script(
        "overseer",
        critique("plan"),
        critique("design"),
        result_report(),
        critique("design"),
        result_report(),
    )
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    points = [
        json.loads(str(c.messages[1]["content"]).split("<data>")[1].split("</data>")[0])["point"]
        for c in _calls(fake, "overseer")
    ]
    assert points == ["plan", "design", "result", "design", "result"]  # no second plan critique
    assert len(_calls(fake, "math_designer")) == 2  # no items: no revision calls


def test_contested_design_item_reaches_the_owner_on_autopilot(
    tmp_path: Path, fake: FakeLLM
) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script(
        "math_designer",
        design(1),
        design_revision(1, STUB, ("C1", "rebutted", "the proposal says x^2")),
    )
    fake.script("coder", *coder_pass(1))
    fake.script(
        "overseer",
        critique("plan"),
        critique("design", item(1, "the formula should be x^3")),
        result_report(),
    )
    ch = FakeChannel(decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch, autopilot=True)
    assert ch.gates == ["roadmap", "result"]  # autopilot would have approved without the item
    assert "rebutted: the proposal says x^2" in ch.requests[-1].markdown


def test_budget_stop_during_a_critique_goes_to_the_budget_gate(
    tmp_path: Path, fake: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broke(*a, **k):
        raise BudgetExceeded("run cap reached")

    monkeypatch.setattr(nodes, "run_critique", broke)
    fake.script("long_planner", ROADMAP)
    ch = FakeChannel(decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["budget"] and "critique_plan" in ch.requests[0].markdown
    assert res.status == "stopped"
