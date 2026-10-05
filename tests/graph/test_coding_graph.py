"""D93 in the graph: the designer writes the formulation and stubs, the coder implements them until
the stub check passes, and the result goes to the owner. No tests, freezing or extra gates."""

from pathlib import Path

from graph_helpers import (
    IMPL,
    PASS,
    REVISE,
    ROADMAP,
    SPEC,
    FakeChannel,
    coder_pass,
    decide,
    design,
    make_deps,
    script_milestone,
)

from r2r.graph.driver import drive, open_graph
from r2r.harness.gitops import Git
from r2r.llm.fake import FakeLLM

PASS_ALL = [decide("approve")] * 3 + [decide("stop")]


def _run(tmp_path: Path, fake: FakeLLM, ch: FakeChannel, **deps_kw):
    deps = make_deps(tmp_path, fake, **deps_kw)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    return deps, res


def _start(fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)


def test_happy_path_design_then_code(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    ch = FakeChannel(decide("approve"), decide("stop"))
    deps, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "result"]  # no tests, design or freeze gates
    ws = deps.workspace()
    assert (ws.root / "src/sq1/core.py").read_text() == IMPL
    git = Git(ws)
    assert git.current_branch() == "main"  # passing work merged into main
    log = git.run("log", "--format=%s", "-3")
    assert "M1: math design (stubs)" in log and "coder session 1 (pass)" in log
    assert not list((ws.root / "tests").rglob("*.py"))  # nothing test-like was written
    d = res.state["digest"]
    assert d.status == "met" and "no benchmark" in d.criteria[0].evidence
    assert not d.criteria[0].checked  # the owner judges
    assert res.state["stubs"] == [{"path": "src/sq1/core.py", "name": "square", "params": ["x"]}]
    assert "Code check" in ch.requests[-1].markdown or "Result · M1" in ch.requests[-1].markdown


def test_stuck_coder_escalates_then_coder_gate(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1))
    # every session: a reply without tools, one nudge, another reply without tools
    fake.script("coder", "a", "b", "c", "d", "e", "Opus: the spec is ambiguous about rounding.")
    ch = FakeChannel(decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    models = [r.model for r in fake.calls if r.role == "coder"]
    assert models == ["anthropic/claude-sonnet-5-5"] * 4 + ["anthropic/claude-opus-5-5"] * 2
    assert ch.gates == ["roadmap", "coder"]
    md = ch.requests[-1].markdown
    assert "3 coder sessions used" in md and "not implemented yet" in md
    assert "the spec is ambiguous about rounding" in md
    assert res.state["coder_sessions"] == 3


def test_coder_gate_approve_reports_the_partial_result(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1))
    fake.script("coder", *"abcdef")
    fake.script("digest", '{"text": "square is still a stub."}')  # the deviations note
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "coder", "result"]
    d = res.state["digest"]
    assert d.status == "partial" and d.deviations == "square is still a stub."  # it imports


def test_escalated_session_can_succeed(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1))
    fake.script("coder", "a", "b", "c", "d", *coder_pass(1))
    ch = FakeChannel(decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "result"]
    assert res.state["coder_sessions"] == 3 and res.state["digest"].status == "met"


def test_second_milestone_builds_on_the_first(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP, ROADMAP)
    script_milestone(fake, 1)
    script_milestone(fake, 2)
    deps, _ = _run(tmp_path, fake, FakeChannel(*PASS_ALL))
    root = deps.workspace().root
    assert (root / "src/sq1/core.py").read_text() == IMPL  # untouched by M2
    assert (root / "src/sq2/core.py").read_text() == IMPL
    second = [r for r in fake.calls if r.role == "math_designer"][1]
    assert "src/sq1/core.py" in str(second.messages[1]["content"])


def test_unusable_design_retried_once_then_back_to_the_planner(
    tmp_path: Path, fake: FakeLLM
) -> None:
    bad = '{"spec": {"formulation": "x"}, "files": []}'  # fails MathDesign validation
    fake.script("long_planner", ROADMAP, ROADMAP)
    fake.script("short_planner", SPEC, SPEC)
    fake.script("coordinator", PASS, PASS)
    fake.script("math_designer", bad, bad, bad, bad, design(1))  # 2 tries x (answer + repair)
    fake.script("coder", *coder_pass(1))
    ch = FakeChannel(decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "result"]
    replan = [r for r in fake.calls if r.role == "long_planner"][1]
    assert "the math designer failed twice" in str(replan.messages[1]["content"])
    assert res.state["digest"].status == "met"


def test_spec_still_failing_review_goes_to_the_designer_with_notes(
    tmp_path: Path, fake: FakeLLM
) -> None:
    """D93: no escalation gate; the designer gets the coordinator's open issues."""
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC, SPEC, SPEC)
    fake.script("coordinator", REVISE, REVISE, REVISE)  # fixture: at most 2 revise rounds
    fake.script("math_designer", design(1))
    fake.script("coder", *coder_pass(1))
    ch = FakeChannel(decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "result"]
    designer = next(r for r in fake.calls if r.role == "math_designer")
    issue = REVISE.issues[0].text
    assert "unresolved notes" in str(designer.messages[1]["content"])
    assert issue in str(designer.messages[1]["content"])
