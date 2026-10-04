"""Phase 5 in the graph: tests first, red before green, frozen tests, coder sessions, tamper."""

from pathlib import Path

from graph_helpers import (
    BROKEN_TESTS,
    GOOD_TESTS,
    IMPL,
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

from r2r.graph.driver import drive, open_graph
from r2r.harness.frozen import verify
from r2r.harness.gitops import Git
from r2r.harness.sandbox import LocalProcessBackend
from r2r.llm.fake import FakeLLM, tool_call

PASS_ALL = [decide("approve")] * 3 + [decide("stop")]


def _run(tmp_path: Path, fake: FakeLLM, ch: FakeChannel, **deps_kw):
    deps = make_deps(tmp_path, fake, **deps_kw)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    return deps, res


def _start(fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", __import__("graph_helpers").PASS)


def test_happy_path_tests_first_then_code(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    deps, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "tests", "result"]
    tests_md = ch.requests[1].markdown
    assert "test_AC1__square_of_three" in tests_md and "Math spec · M1" in tests_md
    ws = deps.workspace()
    verify(ws)  # frozen and intact
    assert (ws.root / "src/sq1/core.py").read_text() == IMPL
    git = Git(ws)
    assert git.current_branch() == "main"  # passing work merged into main
    assert "coder session 1 (pass)" in git.run("log", "--format=%s", "-3")
    d = res.state["digest"]
    assert d.status == "met" and d.criteria[0].evidence == "2/2 tests pass"  # the sanity checks
    assert res.state["test_result"].passed and res.state["coder_sessions"] == 1


def test_vacuous_tests_redesigned_then_design_gate(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1, VACUOUS_TESTS), design(1, VACUOUS_TESTS))
    ch = FakeChannel(decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "design"]
    second = [r for r in fake.calls if r.role == "math_designer"][1]
    assert "AC1" in str(second.messages[1]["content"])  # feedback names the vacuous criterion
    assert "NotImplementedError" in ch.requests[-1].markdown


def test_broken_test_reported_then_fixed(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1, BROKEN_TESTS), design(1))
    fake.script("coder", *coder_pass(1))
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "tests", "result"]
    second = [r for r in fake.calls if r.role == "math_designer"][1]
    assert "wrong reason" in str(second.messages[1]["content"])


def test_tamper_attempt_via_tools_is_blocked_and_counted(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1))
    fake.script(
        "coder",
        [
            tool_call(
                "edit_file",
                path="tests/frozen/sanity/test_sanity_m1.py",
                old="== oracle_square(3)",
                new="== 0",
            )
        ],
        *coder_pass(1),
    )
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    deps, res = _run(tmp_path, fake, ch)
    assert res.state["tamper_attempts"] == 1
    assert ch.gates == ["roadmap", "tests", "result"]
    verify(deps.workspace())


class TamperingBackend(LocalProcessBackend):
    """Simulates code that rewrites a frozen test on disk the first time tests run after freezing
    (impossible in Docker, where those dirs are mounted read-only)."""

    done = False

    def run(self, cmd, timeout_s):
        test_file = self.ws.root / "tests/frozen/sanity/test_sanity_m1.py"
        if "pytest" in cmd and self.ws.frozen_manifest.exists() and not TamperingBackend.done:
            TamperingBackend.done = True
            test_file.chmod(0o644)
            test_file.write_text(
                test_file.read_text().replace("== oracle_square(3)", "== oracle_square(3) or True")
            )
        return super().run(cmd, timeout_s)


def test_tamper_on_disk_goes_to_gate_and_restore(tmp_path: Path, fake: FakeLLM) -> None:
    TamperingBackend.done = False
    _start(fake)
    fake.script("math_designer", design(1))
    fake.script(
        "coder",
        [tool_call("create_file", path="src/sq1/core.py", content=IMPL)],
        "done",
        [tool_call("run_tests")],
    )
    ch = FakeChannel(decide("approve"), decide("approve"), decide("approve"), decide("stop"))
    deps, res = _run(tmp_path, fake, ch, sandbox_factory=TamperingBackend)
    assert ch.gates == ["roadmap", "tests", "tamper", "result"]
    assert "changed: tests/frozen/sanity/test_sanity_m1.py" in ch.requests[2].markdown
    verify(deps.workspace())  # restored from git
    assert res.state["digest"].status == "met"


def test_escalates_then_coder_gate(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1))
    fake.script("coder", "giving up", "still stuck", "no luck with Opus either")
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    models = [r.model for r in fake.calls if r.role == "coder"]
    assert models == ["anthropic/claude-sonnet-5-5"] * 2 + ["anthropic/claude-opus-5-5"]
    assert ch.gates == ["roadmap", "tests", "coder"]
    assert "3 coder sessions used" in ch.requests[-1].markdown
    assert res.state["coder_sessions"] == 3


def test_escalated_session_can_succeed(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1))
    fake.script("coder", "giving up", "still stuck", *coder_pass(1))
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "tests", "result"]
    assert res.state["coder_sessions"] == 3 and res.state["digest"].status == "met"


def test_owner_redirect_at_tests_gate_goes_back_to_designer(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1), design(1))
    fake.script("coder", *coder_pass(1))
    ch = FakeChannel(
        decide("approve"),
        decide("redirect", note="Also test square(0)."),
        decide("approve"),
        decide("stop"),
    )
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "tests", "tests", "result"]
    second = [r for r in fake.calls if r.role == "math_designer"][1]
    assert "Also test square(0)." in str(second.messages[1]["content"])


def test_second_milestone_keeps_first_milestones_tests(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP, ROADMAP)
    script_milestone(fake, 1)
    script_milestone(fake, 2)
    deps, res = _run(tmp_path, fake, FakeChannel(*PASS_ALL))
    root = deps.workspace().root
    assert (root / "tests/frozen/sanity/test_sanity_m1.py").exists()
    assert (root / "tests/frozen/sanity/test_sanity_m2.py").exists()
    assert res.state["test_result"].n_passed == 4  # both milestones' sanity checks run
    existing = [r for r in fake.calls if r.role == "math_designer"][1]
    assert "tests/frozen/sanity/test_sanity_m1.py" in str(existing.messages[1]["content"])


def test_unusable_design_is_a_failed_attempt_not_a_crash(tmp_path: Path, fake: FakeLLM) -> None:
    """pilot-1 M3: #4 named the bench as a module path twice → ContractViolation. That must count
    as a failed design (feedback, one redesign, then the design gate), not crash the run."""
    bad = '{"spec": {"formulation": "x"}, "files": []}'  # fails MathDesign validation
    _start(fake)
    fake.script("math_designer", bad, bad, bad, bad)  # 2 attempts x (answer + repair)
    ch = FakeChannel(decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "design"]
    assert "invalid design" in ch.requests[-1].markdown
    assert res.status == "stopped"


def test_design_without_sanity_check_is_redesigned(tmp_path: Path, fake: FakeLLM) -> None:
    """D83: every milestone needs a sanity check against an independent oracle."""
    _start(fake)
    fake.script("math_designer", design(1, sanity=False), design(1))
    fake.script("coder", *coder_pass(1))
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "tests", "result"]
    second = [r for r in fake.calls if r.role == "math_designer"][1]
    assert "AC1 has no sanity check" in str(second.messages[1]["content"])


def test_oracle_that_calls_the_package_is_redesigned(tmp_path: Path, fake: FakeLLM) -> None:
    cheat = GOOD_TESTS.replace("return x * x", "return square(x)")
    _start(fake)
    fake.script("math_designer", design(1, cheat), design(1))
    fake.script("coder", *coder_pass(1))
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    second = [r for r in fake.calls if r.role == "math_designer"][1]
    assert "oracle_square uses square from the package" in str(second.messages[1]["content"])
