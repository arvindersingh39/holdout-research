"""#4 design writing and #5 coder tools/sessions with FakeLLM and LocalProcessBackend."""

import json
from pathlib import Path

import pytest
from samples import milestone_spec

from r2r.agents.coder import CoderTools, run_coder_session
from r2r.agents.math_designer import DesignError, write_design
from r2r.contracts import BenchSpec, FileSpec, MathDesign, MathSpec, SanityCheck
from r2r.harness.frozen import freeze, verify
from r2r.harness.sandbox import LocalProcessBackend
from r2r.harness.workspace import Workspace
from r2r.llm.fake import FakeLLM, tool_call
from r2r.llm.gateway import CallContext
from r2r.store.runs import run_paths

META = {"created_by": "math_designer", "run_id": "run-test", "milestone_id": "M1"}
TEST_FILE = """\
from sq.core import square


def oracle_square(x):
    return x * x


def test_AC1__S1_square_of_three():
    assert square(3) == oracle_square(3) == 9


def test_AC1__S1_square_of_negative():
    assert square(-2) == oracle_square(-2)
"""
SANITY = "tests/frozen/sanity/test_sanity_m1.py"
STUB = "def square(x: float) -> float:\n    raise NotImplementedError\n"
IMPL = "def square(x: float) -> float:\n    return x * x\n"


def design() -> MathDesign:
    spec = MathSpec(
        **META,
        formulation="f(x) = x^2",
        algorithm="multiply",
        signatures=["square(x: float) -> float"],
        test_files=[SANITY],
        sanity_checks=[
            SanityCheck(
                id="S1",
                criterion_ids=["AC1"],
                kind="limit_case",
                statement="square agrees with x*x",
                file=SANITY,
            )
        ],
        bench=BenchSpec(entrypoint="bench/bench_main.py", problem_size="1e5 calls"),
    )
    files = [
        FileSpec(path=SANITY, purpose="test", content=TEST_FILE),
        FileSpec(path="src/sq/__init__.py", purpose="stub", content=""),
        FileSpec(path="src/sq/core.py", purpose="stub", content=STUB),
        FileSpec(
            path="bench/bench_main.py",
            purpose="bench",
            content="from sq.core import square\n\n\ndef main():\n    square(2.0)\n",
        ),
    ]
    return MathDesign(**META, spec=spec, files=files)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return Workspace.create(run_paths("run-test", base=tmp_path, create=True))


@pytest.fixture
def tools(ws: Workspace) -> CoderTools:
    write_design(ws, design(), previous=None)
    freeze(ws)
    return CoderTools(
        ws, LocalProcessBackend(ws), run_id="run-test", milestone_id="M1", timeout_s=60
    )


def _call(tools: CoderTools, name: str, **args) -> str:
    from r2r.llm.tools import dispatch

    return dispatch(tool_call(name, **args), {t.name: t for t in tools.tools()}).output


# --- design writing -------------------------------------------------------------------------


def test_write_design_writes_files(ws: Workspace) -> None:
    written = write_design(ws, design(), previous=None)
    assert SANITY in written
    assert (ws.root / "src/sq/core.py").read_text() == STUB


def test_design_cannot_overwrite_existing_src_or_frozen(ws: Workspace) -> None:
    write_design(ws, design(), previous=None)
    freeze(ws)
    again = design()
    with pytest.raises(DesignError, match="frozen"):
        write_design(ws, again, previous=None)  # the sanity file is frozen now


def test_redesign_replaces_previous_attempt(ws: Workspace) -> None:
    first = design()
    write_design(ws, first, previous=None)
    second = design()
    write_design(ws, second, previous=first.spec)  # same paths: previous attempt's files removed
    assert (ws.root / "src/sq/core.py").read_text() == STUB


def test_design_path_attack_refused(ws: Workspace) -> None:
    bad = design()
    data = bad.model_dump()
    data["files"].append({"path": "pyproject.toml", "purpose": "stub", "content": "x"})
    with pytest.raises(DesignError):
        write_design(ws, MathDesign.model_validate(data), previous=None)


# --- coder tools ------------------------------------------------------------------------------


def test_list_and_read(tools: CoderTools) -> None:
    listing = _call(tools, "list_files", dir=".")
    assert "src/sq/core.py" in listing and SANITY in listing
    assert "NotImplementedError" in _call(tools, "read_file", path="src/sq/core.py")
    assert "test_AC1__S1_square_of_three" in _call(tools, "read_file", path=SANITY)


def test_read_file_line_cap(tools: CoderTools) -> None:
    big = "\n".join(f"x{i} = {i}" for i in range(1000))
    (tools.ws.root / "src/sq/big.py").write_text(big)  # longer than create_file allows
    out = _call(tools, "read_file", path="src/sq/big.py", start=1, end=1000)
    assert out.count("\n") <= 401 and "x399 = 399" in out and "x400 = 400" not in out


@pytest.mark.parametrize("path", ["../outside.py", "/etc/passwd", "src/../../x.py"])
def test_read_path_attacks(tools: CoderTools, path: str) -> None:
    assert _call(tools, "read_file", path=path).startswith("error")


def test_edit_requires_unique_match(tools: CoderTools) -> None:
    assert "edited" in _call(
        tools,
        "edit_file",
        path="src/sq/core.py",
        old="raise NotImplementedError",
        new="return x * x",
    )
    assert "not found" in _call(tools, "edit_file", path="src/sq/core.py", old="nope", new="x")
    _call(tools, "create_file", path="src/sq/dup.py", content="a = 1\na = 1\n")
    assert "2 times" in _call(tools, "edit_file", path="src/sq/dup.py", old="a = 1", new="b = 2")


def test_create_file_limits(tools: CoderTools) -> None:
    too_long = "\n".join("pass" for _ in range(601))
    assert "600 lines" in _call(tools, "create_file", path="src/sq/long.py", content=too_long)
    assert _call(tools, "create_file", path="figures/plot.py", content="x").startswith("error")


def test_frozen_writes_blocked_and_logged(tools: CoderTools, ws: Workspace) -> None:
    out = _call(tools, "edit_file", path=SANITY, old="== 9", new="== 0")
    assert out.startswith("error") and "read-only" in out
    out = _call(tools, "create_file", path="checks/check_evil.py", content="pass")
    assert out.startswith("error")
    assert len(tools.tamper_attempts) == 2
    verify(ws)  # the frozen hash is intact


def test_run_tests_tool(tools: CoderTools) -> None:
    out = json.loads(_call(tools, "run_tests"))
    assert out["passed"] is False and out["n_failed"] == 2
    assert tools.last_result is not None and not tools.last_result.passed


# --- coder session ------------------------------------------------------------------------------


def test_session_stops_when_tests_pass(tools: CoderTools, ctx: CallContext, fake: FakeLLM) -> None:
    fake.script(
        "coder",
        [
            tool_call(
                "edit_file",
                path="src/sq/core.py",
                old="raise NotImplementedError",
                new="return x * x",
            )
        ],
        [tool_call("run_tests")],
        "should never be reached",
    )
    session = run_coder_session(milestone_spec(), design().spec, None, tools, ctx)
    assert session.loop.stopped_by == "stop_fn"
    assert tools.last_result is not None and tools.last_result.passed
    assert fake.pending("coder") == 1
    assert fake.calls[0].model == "anthropic/claude-sonnet-5-5"


def test_session_uses_escalation_model(tools: CoderTools, ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("coder", "giving up")
    run_coder_session(
        milestone_spec(), design().spec, None, tools, ctx, model="anthropic/claude-opus-5-5"
    )
    assert fake.calls[0].model == "anthropic/claude-opus-5-5"
