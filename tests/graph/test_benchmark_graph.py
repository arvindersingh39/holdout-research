"""D93 in the graph: benchmarks. The owner's recorded values and the proposal's results are the
reference; milestones name the refs they reproduce; the coder sees refs only and writes a
reproduce script; the owner runs it at the benchmark gate; plain Python compares."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from graph_helpers import (
    BRIEF,
    IMPL,
    PASS,
    PILOT,
    ROADMAP,
    SPEC,
    FakeChannel,
    decide,
    design,
    make_deps,
    reproduce,
)

from r2r.graph.driver import drive, open_graph
from r2r.graph.gates import GateRequest
from r2r.llm.fake import FakeLLM, tool_call

RECORDED = {
    "kappa_realised": [3.6, 4.8, 6.2],
    "graph12": {"R0_at_B0": 12.6, "edges": 112},
    "tolerances": {"graph12.R0_at_B0": 0.05},
}
ROADMAP_B = ROADMAP.model_copy(
    update={"milestones": [ROADMAP.milestones[0].model_copy(update={"benchmarks": ["graph12"]})]}
)


def _proposal(tmp_path: Path) -> Path:
    p = tmp_path / "proposal"
    shutil.copytree(PILOT, p)
    (p / "data").mkdir()
    (p / "data" / "recorded.json").write_text(json.dumps(RECORDED))
    return p


class Owner(FakeChannel):
    """Runs the reproduce script at the benchmark gate, like the owner would."""

    def __init__(self, ws_root: Path, *decisions, run: bool = True) -> None:
        super().__init__(*decisions)
        self.root, self.run = ws_root, run

    def ask(self, req: GateRequest, run_id: str):
        if req.gate == "benchmark" and self.run:
            script = req.markdown.split("python ", 1)[1].split()[0]
            env = {"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"}
            subprocess.run([sys.executable, script], cwd=self.root, env=env, check=True)
        return super().ask(req, run_id)


def _payload(fake: FakeLLM, role: str, i: int = 0) -> str:
    return str([c for c in fake.calls if c.role == role][i].messages[1]["content"])


def _script(fake: FakeLLM, *coder) -> None:
    fake.script("long_planner", ROADMAP_B)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", PASS)
    fake.script("math_designer", design(1))
    fake.script("coder", *coder)


def _implement(values: dict) -> list:
    return [
        [tool_call("create_file", path="src/sq1/core.py", content=IMPL)],
        *reproduce(1, values),
        [tool_call("check_code")],
    ]


def _drive(tmp_path: Path, fake: FakeLLM, *decisions, run: bool = True):
    deps = make_deps(tmp_path, fake, proposal=_proposal(tmp_path))
    ch = Owner(deps.workspace().root, *decisions, run=run)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    return deps, ch, res


def test_reproduced_values_are_verified(tmp_path: Path, fake: FakeLLM) -> None:
    _script(fake, *_implement({"graph12.R0_at_B0": 12.62, "graph12.edges": 112}))
    deps, ch, res = _drive(tmp_path, fake, decide("approve"), decide("approve"), decide("stop"))
    assert ch.gates == ["roadmap", "benchmark", "result"]
    gate = ch.requests[1].markdown
    assert "bench/reproduce_m1.py" in gate and "results/benchmarks/m1.json" in gate
    assert "graph12.R0_at_B0" in gate and "12.6" not in gate  # refs, not values
    assert "Benchmarks: verified (2 of 2" in ch.requests[2].markdown
    assert res.state["digest"].status == "met"
    # the reference never reaches the designer, the coder or the workspace
    for role in ("math_designer", "coder"):
        text = _payload(fake, role)
        assert "graph12.R0_at_B0" in text and "12.6" not in text
    assert "graph12.R0_at_B0" in _payload(fake, "long_planner")  # the outline of refs
    assert not list(deps.workspace().root.rglob("recorded.json"))


def test_mismatch_goes_back_to_the_coder_then_the_owner_runs_again(
    tmp_path: Path, fake: FakeLLM
) -> None:
    wrong = _implement({"graph12.R0_at_B0": 13.9, "graph12.edges": 112})
    fixed = [
        *reproduce(1, {"graph12.R0_at_B0": 12.59, "graph12.edges": 112}),
        [tool_call("check_code")],
    ]
    _script(fake, *wrong, *fixed)
    _, ch, res = _drive(
        tmp_path, fake, decide("approve"), decide("approve"), decide("approve"), decide("stop")
    )
    assert ch.gates == ["roadmap", "benchmark", "benchmark", "result"]
    fix_round = _payload(fake, "coder", len(wrong))
    assert "generated 13.9, recorded 12.6" in fix_round  # values only after the owner's run
    assert res.state["verification"].status == "verified" and res.state["fix_rounds"] == 1


def test_missing_results_file_asks_again(tmp_path: Path, fake: FakeLLM) -> None:
    _script(fake, *_implement({"graph12.R0_at_B0": 12.6001, "graph12.edges": 112}))
    _, ch, _ = _drive(
        tmp_path,
        fake,
        decide("approve"),
        decide("approve"),  # nothing was run: no file
        decide("approve", note="elsewhere/out.json"),
        decide("stop"),
        run=False,
    )
    assert ch.gates[:3] == ["roadmap", "benchmark", "benchmark"]
    assert "could not be used" in ch.requests[2].markdown and "not found" in ch.requests[2].markdown
    assert "elsewhere/out.json not found" in ch.requests[3].markdown  # a named path, also missing
    assert ch.gates == ["roadmap", "benchmark", "benchmark", "benchmark"]


def test_owner_file_at_another_path(tmp_path: Path, fake: FakeLLM) -> None:
    _script(fake, *_implement({"graph12.R0_at_B0": 12.6001, "graph12.edges": 112}))
    deps = make_deps(tmp_path, fake, proposal=_proposal(tmp_path))
    root = deps.workspace().root
    out = tmp_path / "mine.json"
    out.write_text(
        json.dumps(
            {
                "entries": [
                    {"ref": "graph12.R0_at_B0", "generated": 12.61},
                    {"ref": "graph12.edges", "generated": 112},
                ]
            }
        )
    )
    ch = Owner(root, decide("approve"), decide("approve", note=str(out)), decide("stop"), run=False)
    with open_graph(deps) as graph:
        res = drive(graph, deps.run_id, ch, start=True)
    assert ch.gates == ["roadmap", "benchmark", "result"]
    assert res.state["verification"].status == "verified"


def test_proposal_benchmarks_are_stored_and_shown_at_the_roadmap(tmp_path: Path) -> None:
    brief = json.loads(BRIEF) | {
        "benchmarks": [
            {
                "id": "BM1",
                "quantity": "call price",
                "value": 10.45,
                "units": "USD",
                "tolerance": 0.005,
                "conditions": "S=K=100",
                "source": "proposal",
                "quote": "a price of 10.45 at the money",
            }
        ]
    }
    fake = FakeLLM()
    fake.script("retriever", json.dumps(brief))
    rm = ROADMAP.model_copy(
        update={
            "milestones": [
                ROADMAP.milestones[0].model_copy(update={"benchmarks": ["benchmarks.BM1"]})
            ]
        }
    )
    fake.script("long_planner", rm)
    deps = make_deps(tmp_path, fake)
    ch = FakeChannel(decide("stop"))
    with open_graph(deps) as graph:
        drive(graph, deps.run_id, ch, start=True)
    stored = json.loads((deps.paths.root / "data/benchmarks.json").read_text())
    assert stored["benchmarks"] == {"BM1": 10.45}
    assert stored["tolerances"] == {"benchmarks.BM1": 0.005}
    md = ch.requests[0].markdown
    assert "benchmarks you will run: benchmarks.BM1" in md
    assert 'proposal: "a price of 10.45 at the money"' in md
    assert "quote not found" in md  # the pilot proposal says no such thing
    assert "benchmarks.BM1: call price" in _payload(fake, "long_planner")
