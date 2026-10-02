"""Phase 7b in the graph: baselines, reference check, test disputes, the coder's last message."""

import json
import sys
from pathlib import Path

import httpx
from graph_helpers import (
    IMPL,
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
from r2r.harness.frozen import verify
from r2r.harness.gitops import Git
from r2r.llm.fake import FakeLLM, tool_call
from r2r.search.tavily import TavilyClient

sys.path.insert(0, str(Path(__file__).parents[1] / "unit"))
from test_scout import KEY, FakeTavily

PAGE_QUOTE = "the Black-Scholes call price is 10.4506"
WRONG_TESTS = (
    "from {pkg}.core import square\n\n\ndef test_AC1__wrong():\n    assert square(3) == 10\n"
)


def _start(fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)


def _run(tmp_path: Path, fake: FakeLLM, ch: FakeChannel, search=None):
    deps = make_deps(tmp_path, fake)
    deps.search_client = search
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    return deps, res


def _designer_payload(fake: FakeLLM, i: int = 0) -> str:
    return str([r for r in fake.calls if r.role == "math_designer"][i].messages[1]["content"])


def test_verified_baselines_reach_designer_and_tests_gate(tmp_path: Path, fake: FakeLLM) -> None:
    tav = FakeTavily()
    search = TavilyClient(KEY, http=httpx.Client(transport=httpx.MockTransport(tav.handler)))
    _start(fake)
    fake.script(
        "scout",
        '{"queries": ["black scholes atm reference price"]}',
        json.dumps(
            {
                "cards": [
                    {
                        "criterion_id": "AC1",
                        "claim": "ATM call",
                        "value": 10.4506,
                        "units": "USD",
                        "source_url": "https://example.org/bs1",
                        "quote": PAGE_QUOTE,
                    }
                ]
            }
        ),
    )
    fake.script("math_designer", design(1))
    fake.script("coder", *coder_pass(1))
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch, search=search)
    assert "10.4506" in _designer_payload(fake) and "example.org/bs1" in _designer_payload(fake)
    tests_md = ch.requests[1].markdown
    assert "Published baselines" in tests_md and PAGE_QUOTE in tests_md
    assert "passes on the designer's reference solution" in tests_md


def test_no_key_note_reaches_designer(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1))
    fake.script("coder", *coder_pass(1))
    _run(tmp_path, fake, FakeChannel(decide("approve"), decide("approve"), decide("stop")))
    assert "No TAVILY_API_KEY" in _designer_payload(fake)


def test_unsatisfiable_design_redesigned_then_gate(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1, WRONG_TESTS), design(1, WRONG_TESTS))
    ch = FakeChannel(decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "design"]
    assert "can never pass" in _designer_payload(fake, 1)
    assert "can never pass" in ch.requests[-1].markdown


def test_dispute_approved_revision_then_code_passes(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1), design(1))  # original, then the revision
    fake.script(
        "coder",
        [
            tool_call(
                "dispute_test",
                test_id="tests.frozen.test_m1_square::test_AC1__square_of_three",
                reason="I believe 9 is wrong",
                evidence="3*3",
            )
        ],
        *coder_pass(1),
    )
    ch = FakeChannel(decide("approve"), decide("approve"), decide("approve"), decide("stop"))
    deps, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "tests", "dispute", "result"]
    dmd = ch.requests[2].markdown
    assert "I believe 9 is wrong" in dmd and "test_AC1__square_of_three" in dmd
    ws = deps.workspace()
    verify(ws)  # re-frozen after the owner-approved revision
    assert "owner-approved revision" in Git(ws).run("log", "--format=%s", "-5")
    assert res.state["digest"].status == "met"
    assert (ws.root / "src/sq1/core.py").read_text() == IMPL


def test_dispute_revision_failing_on_reference_goes_to_coder_gate(
    tmp_path: Path, fake: FakeLLM
) -> None:
    _start(fake)
    fake.script("math_designer", design(1), design(1, WRONG_TESTS))
    fake.script(
        "coder",
        [
            tool_call(
                "dispute_test",
                test_id="tests.frozen.test_m1_square::test_AC1__square_of_three",
                reason="r",
                evidence="e",
            )
        ],
    )
    ch = FakeChannel(decide("approve"), decide("approve"), decide("approve"), decide("stop"))
    deps, _ = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "tests", "dispute", "coder"]
    assert "still fails on the reference" in ch.requests[-1].markdown
    verify(deps.workspace())  # original frozen test restored


def test_coder_gate_shows_last_message(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1))
    fake.script("coder", "stuck", "still stuck", "Opus: the spec is ambiguous about rounding.")
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates[-1] == "coder"
    assert "the spec is ambiguous about rounding" in ch.requests[-1].markdown


def test_dispute_redirect_and_stop(tmp_path: Path, fake: FakeLLM) -> None:
    _start(fake)
    fake.script("math_designer", design(1))
    fake.script("coder", [tool_call("dispute_test", test_id="t::x", reason="r", evidence="e")])
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "tests", "dispute"] and res.status == "stopped"
