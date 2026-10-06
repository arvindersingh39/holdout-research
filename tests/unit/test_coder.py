"""#4 design writing and #5 coder tools/sessions with FakeLLM and LocalProcessBackend."""

import json
from pathlib import Path

import pytest
from samples import milestone_spec

from r2r.agents.coder import CoderTools, run_coder_session
from r2r.agents.math_designer import DesignError, write_design
from r2r.contracts import BenchSpec, FileSpec, MathDesign, MathSpec
from r2r.harness.gitops import Git
from r2r.harness.sandbox import LocalProcessBackend
from r2r.harness.stubcheck import record_stubs
from r2r.harness.workspace import Workspace
from r2r.llm.fake import FakeLLM, tool_call
from r2r.llm.gateway import CallContext
from r2r.store.runs import run_paths

META = {"created_by": "math_designer", "run_id": "run-test", "milestone_id": "M1"}
STUB = "def square(x: float) -> float:\n    raise NotImplementedError\n"
IMPL = "def square(x: float) -> float:\n    return x * x\n"


def design() -> MathDesign:
    spec = MathSpec(
        **META,
        formulation="f(x) = x^2",
        algorithm="multiply",
        signatures=["square(x: float) -> float"],
        bench=BenchSpec(entrypoint="bench/bench_main.py", problem_size="1e5 calls"),
    )
    files = [
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
    written = write_design(ws, design(), previous=None)
    return CoderTools(
        ws,
        LocalProcessBackend(ws),
        run_id="run-test",
        milestone_id="M1",
        timeout_s=60,
        stubs=record_stubs(ws.root, written),
    )


def _call(tools: CoderTools, name: str, **args) -> str:
    from r2r.llm.tools import dispatch

    return dispatch(tool_call(name, **args), {t.name: t for t in tools.tools()}).output


# --- design writing -------------------------------------------------------------------------


def test_write_design_writes_stubs(ws: Workspace) -> None:
    written = write_design(ws, design(), previous=None)
    assert "src/sq/core.py" in written and "bench/bench_main.py" in written
    assert (ws.root / "src/sq/core.py").read_text() == STUB


def test_design_cannot_overwrite_committed_code(ws: Workspace) -> None:
    write_design(ws, design(), previous=None)
    Git(ws).commit("M1 stubs")
    with pytest.raises(DesignError, match="already exists"):
        write_design(ws, design(), previous=None)  # earlier milestones' code stays


def test_redesign_replaces_previous_attempt(ws: Workspace) -> None:
    first = design()
    write_design(ws, first, previous=None)
    second = design()
    write_design(ws, second, previous=first.spec)  # same paths: previous attempt's files removed
    assert (ws.root / "src/sq/core.py").read_text() == STUB


def test_design_path_attack_refused() -> None:
    data = design().model_dump()
    data["files"].append({"path": "src/../../evil.py", "purpose": "stub", "content": "x"})
    with pytest.raises(ValueError, match="inside the workspace"):
        MathDesign.model_validate(data)


# --- coder tools ------------------------------------------------------------------------------


def test_list_and_read(tools: CoderTools) -> None:
    listing = _call(tools, "list_files", dir=".")
    assert "src/sq/core.py" in listing and "bench/bench_main.py" in listing
    assert "NotImplementedError" in _call(tools, "read_file", path="src/sq/core.py")


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
    assert _call(tools, "create_file", path="figures/plot.py", content="x").startswith("wrote")


def test_writes_outside_the_code_folders_are_refused(tools: CoderTools) -> None:
    for path in ("tests/test_x.py", "pyproject.toml", "results/benchmarks/m1.json"):
        assert _call(tools, "create_file", path=path, content="x").startswith("error")
    assert len(tools.blocked_writes) == 3


def test_check_code_tool(tools: CoderTools) -> None:
    out = json.loads(_call(tools, "check_code"))
    assert out["passed"] is False and "not implemented" in out["failures"][0]["message"]
    assert tools.last_result is not None and not tools.last_result.passed


# --- coder session ------------------------------------------------------------------------------


def test_session_stops_when_the_check_passes(
    tools: CoderTools, ctx: CallContext, fake: FakeLLM
) -> None:
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
        [tool_call("check_code")],
        "should never be reached",
    )
    session = run_coder_session(milestone_spec(), design().spec, None, tools, ctx)
    assert session.loop.stopped_by == "stop_fn"
    assert tools.last_result is not None and tools.last_result.passed
    assert fake.pending("coder") == 1
    assert fake.calls[0].model == "anthropic/claude-sonnet-5-5"


def test_session_uses_escalation_model(tools: CoderTools, ctx: CallContext, fake: FakeLLM) -> None:
    fake.script("coder", "giving up", "still giving up")  # one nudge, then the session ends
    run_coder_session(
        milestone_spec(), design().spec, None, tools, ctx, model="anthropic/claude-opus-5-5"
    )
    assert fake.calls[0].model == "anthropic/claude-opus-5-5"


def test_coder_sees_benchmark_refs_never_values(
    tools: CoderTools, ctx: CallContext, fake: FakeLLM
) -> None:
    """D93: the reproduce script and results file are named; mismatches arrive on a fix round."""
    fake.script("coder", "done", "done")
    tools.reproduce = "bench/reproduce_m1.py"
    bench = [
        {"ref": "benchmarks.BM1", "quantity": "attack rate", "units": "", "conditions": "N=20"}
    ]
    run_coder_session(
        milestone_spec(),
        design().spec,
        None,
        tools,
        ctx,
        benchmarks=bench,
        results_file="results/benchmarks/m1.json",
        mismatches="- benchmarks.BM1: generated 0.5, recorded 0.41 (±0.005)",
    )
    user = next(m for m in fake.calls[0].messages if m["role"] == "user")["content"]
    text = user if isinstance(user, str) else "".join(b.get("text", "") for b in user)
    payload = json.loads(text[text.index("{") : text.rindex("}") + 1])
    assert payload["benchmarks"] == bench and payload["reproduce_script"] == "bench/reproduce_m1.py"
    assert payload["results_file"] == "results/benchmarks/m1.json"
    assert "recorded 0.41" in payload["mismatches"]


def test_binary_files_are_described_not_decoded(tools: CoderTools) -> None:
    """Saved tensors (.pt) and other binary outputs: the coder gets a note, not a crash."""
    (tools.ws.root / "results").mkdir(exist_ok=True)
    (tools.ws.root / "results/net.pt").write_bytes(b"PK\x03\x04\x00\xff\xfe" * 100)
    out = _call(tools, "read_file", path="results/net.pt")
    assert "binary file" in out and "700 bytes" in out and not out.startswith("error")


def test_coder_may_write_command_line_scripts(tools: CoderTools) -> None:
    out = _call(tools, "create_file", path="scripts/report.py", content="print('r')\n")
    assert out.startswith("wrote") and (tools.ws.root / "scripts/report.py").is_file()
