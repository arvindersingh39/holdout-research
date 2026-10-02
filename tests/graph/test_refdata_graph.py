"""D71 in the graph: the owner's reference data reaches the designer and planner verbatim and is
frozen with the tests; designs may not overwrite it."""

import json
import shutil
from pathlib import Path

from graph_helpers import PILOT, ROADMAP, FakeChannel, decide, design, make_deps, script_milestone

from r2r.graph.driver import drive, open_graph
from r2r.harness.frozen import FrozenTamper, verify
from r2r.llm.fake import FakeLLM

RECORDED = {"kappa_realised": [3.6, 4.8, 6.2], "graph12": {"R0_at_B0": 12.6}}


def _proposal(tmp_path: Path) -> Path:
    p = tmp_path / "proposal"
    shutil.copytree(PILOT, p)
    (p / "data").mkdir()
    (p / "data" / "recorded.json").write_text(json.dumps(RECORDED))
    return p


def _payload(fake: FakeLLM, role: str, i: int = 0) -> str:
    return str([c for c in fake.calls if c.role == role][i].messages[1]["content"])


def test_reference_data_reaches_agents_and_is_frozen(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    deps = make_deps(tmp_path, fake, proposal=_proposal(tmp_path))
    with open_graph(deps) as graph:
        drive(
            graph,
            deps.run_id,
            FakeChannel(decide("approve"), decide("approve"), decide("stop")),
            start=True,
        )
    designer = _payload(fake, "math_designer")
    assert "tests/frozen/data/recorded.json" in designer and "12.6" in designer
    assert "tests/frozen/data/recorded.json" in _payload(fake, "short_planner")
    ws = deps.workspace()
    data = ws.root / "tests/frozen/data/recorded.json"
    assert json.loads(data.read_text()) == RECORDED
    verify(ws)  # frozen with the tests
    data.chmod(0o644)
    data.write_text("{}")
    try:
        verify(ws)
        raise AssertionError("tampering with reference data must be detected")
    except FrozenTamper:
        pass


def test_design_may_not_overwrite_reference_data(tmp_path: Path, fake: FakeLLM) -> None:
    from r2r.contracts import FileSpec

    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", __import__("graph_helpers").SPEC)
    fake.script("coordinator", __import__("graph_helpers").PASS)
    bad = design(1)
    bad = bad.model_copy(
        update={
            "files": [
                *bad.files,
                FileSpec(path="tests/frozen/data/recorded.json", purpose="test", content="{}"),
            ]
        }
    )
    fake.script("math_designer", bad, bad)
    deps = make_deps(tmp_path, fake, proposal=_proposal(tmp_path))
    ch = FakeChannel(decide("approve"), decide("stop"))
    with open_graph(deps) as graph:
        drive(graph, deps.run_id, ch, start=True)
    assert ch.gates == ["roadmap", "design"]
    assert "owner's reference data" in ch.requests[-1].markdown
    assert (
        json.loads((deps.workspace().root / "tests/frozen/data/recorded.json").read_text())
        == RECORDED
    )
