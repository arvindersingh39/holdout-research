"""The planning graph end to end with FakeLLM: routing, gates, stop and resume.

Milestones run the real design → code → digest steps (D93) in the unit-test sandbox
(LocalProcessBackend).
"""

import subprocess
import sys
from pathlib import Path

from graph_helpers import (
    REPO,
    REVISE,
    REVISE_ROADMAP,
    ROADMAP,
    SPEC,
    FakeChannel,
    coder_pass,
    decide,
    design,
    make_deps,
    script_milestone,
)

from r2r.contracts import Roadmap
from r2r.graph.driver import drive, open_graph, pending_gate
from r2r.llm.fake import FakeLLM


def _run(tmp_path: Path, fake: FakeLLM, channel: FakeChannel):
    deps = make_deps(tmp_path, fake)
    with open_graph(deps) as graph:
        result = drive(graph, deps.run_id, channel, start=True)
    return deps, result


def _payloads(fake: FakeLLM, role: str) -> list[str]:
    def text(m: dict) -> str:
        c = m["content"]
        return c if isinstance(c, str) else "".join(b.get("text", "") for b in c)

    return [text(req.messages[1]) for req in fake.calls if req.role == role]


def test_full_pass_path_to_completion(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)  # met results need no replan (leak 3)
    script_milestone(fake, 1)
    script_milestone(fake, 2)
    ch = FakeChannel(*[decide("approve")] * 3)
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "result", "result"]  # later rewrites aren't material
    assert res.status == "complete"
    rm: Roadmap = res.state["roadmap"]
    assert [m.status for m in rm.milestones] == ["done", "done"]
    assert rm.version == 1  # one planner call: the scripted roadmap is used up, no more allowed
    roles = ("long_planner", "short_planner", "coordinator", "math_designer", "coder")
    assert all(fake.pending(r) == 0 for r in roles)


def test_specs_get_context_refs_and_milestone_ids(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP, ROADMAP)
    script_milestone(fake, 1)
    script_milestone(fake, 2)
    ch = FakeChannel(decide("approve"), decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    spec = res.state["spec"]
    assert spec.milestone_id == "M2"
    assert spec.context_refs  # filled by code from the context pack
    assert "<document" in _payloads(fake, "short_planner")[0]


def test_revise_once_then_pass(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", REVISE)
    script_milestone(fake, 1)
    ch = FakeChannel(decide("approve"), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "result"]
    assert "Units missing for sigma." in _payloads(fake, "short_planner")[1]
    assert res.status == "stopped"


def test_revise_limit_builds_anyway_with_the_open_issues(tmp_path: Path, fake: FakeLLM) -> None:
    """D93: no escalation gate; after the revise rounds the designer gets the open issues."""
    fake.script("long_planner", ROADMAP)
    fake.script("short_planner", SPEC, SPEC, SPEC)
    fake.script("coordinator", REVISE, REVISE, REVISE)  # 2 revision rounds allowed
    fake.script("math_designer", design(1))
    fake.script("coder", *coder_pass(1))
    ch = FakeChannel(decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "result"]
    assert "Units missing for sigma." in _payloads(fake, "math_designer")[0]


def test_roadmap_issue_routes_to_long_planner(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP, ROADMAP)
    fake.script("short_planner", SPEC)
    fake.script("coordinator", REVISE_ROADMAP)
    script_milestone(fake, 1)
    ch = FakeChannel(decide("approve"), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "result"]  # unchanged roadmap: no second roadmap gate
    assert "R2: M1 is too big." in _payloads(fake, "long_planner")[1]


def test_redirect_note_reaches_long_planner(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP, ROADMAP)
    ch = FakeChannel(decide("redirect", note="Add an Asian option milestone."), decide("stop"))
    _run(tmp_path, fake, ch)
    assert ch.gates == ["roadmap", "roadmap"]  # still the first roadmap: needs approval
    assert "Add an Asian option milestone." in _payloads(fake, "long_planner")[1]


def test_stop_ends_run(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    ch = FakeChannel(decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert res.status == "stopped"
    assert _payloads(fake, "short_planner") == []


def test_edit_replaces_roadmap(tmp_path: Path, fake: FakeLLM) -> None:
    edited = Roadmap.model_validate({**ROADMAP.model_dump(), "goal": "Edited goal by the owner."})
    fake.script("long_planner", ROADMAP)
    script_milestone(fake, 1)
    ch = FakeChannel(decide("edit", edited=edited), decide("stop"))
    _, res = _run(tmp_path, fake, ch)
    assert res.state["roadmap"].goal == "Edited goal by the owner."
    assert res.state["approved_roadmap"].goal == "Edited goal by the owner."


def test_gate_payloads_are_rendered(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    ch = FakeChannel(decide("stop"))
    _run(tmp_path, fake, ch)
    req = ch.requests[0]
    assert "Roadmap v1" in req.markdown
    assert req.contract_type == "Roadmap"


def test_quit_then_resume_in_fresh_graph(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    _, res = _run(tmp_path, fake, FakeChannel(None))
    assert res.status == "paused"

    fresh = FakeLLM()  # a new process: nothing in memory carries over
    script_milestone(fresh, 1)
    deps2 = make_deps(tmp_path, fresh, create=False)
    with open_graph(deps2) as graph:
        assert graph.get_state({"configurable": {"thread_id": "g"}}).next == ("roadmap_gate",)
        assert pending_gate(graph, "g").gate == "roadmap"
        ch = FakeChannel(decide("approve"), decide("stop"))
        res2 = drive(graph, "g", ch, start=False)
    assert ch.gates == ["roadmap", "result"]
    assert res2.status == "stopped"
    assert fresh.calls[0].role == "short_planner"  # no re-run of retriever or long planner


def test_status_in_subprocess(tmp_path: Path, fake: FakeLLM) -> None:
    fake.script("long_planner", ROADMAP)
    _run(tmp_path, fake, FakeChannel(None))
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from r2r.cli import app; app()",
            "status",
            "g",
            "--runs-base",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert "roadmap" in out.stdout.lower()
    assert "Waiting at gate" in out.stdout
