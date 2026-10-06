"""D95: after the owner approves a milestone, its code and a readable summary are copied to the
export folder, so results can be analysed along the way. Plain code; no LLM."""

from pathlib import Path

import pytest
from samples import math_spec, milestone_spec, roadmap

from r2r.contracts import CriterionResult, Critique, CritiqueItem, CritiqueResponse, ResultDigest
from r2r.harness.gitops import Git
from r2r.harness.workspace import Workspace
from r2r.publish.export import export_milestone, slug
from r2r.store.runs import run_paths


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    ws = Workspace.create(run_paths("r", base=tmp_path, create=True))
    (ws.root / "src/pkg").mkdir(parents=True)
    (ws.root / "src/pkg/core.py").write_text("def f():\n    return 1\n")
    (ws.root / "src/pkg/__pycache__").mkdir()
    (ws.root / "src/pkg/__pycache__/core.pyc").write_bytes(b"x")
    (ws.root / "results").mkdir()
    (ws.root / "results/table.json").write_text("{}")
    Git(ws).commit("M1 code")
    return ws


def _digest() -> ResultDigest:
    return ResultDigest(
        created_by="digest",
        run_id="r",
        milestone_id="M1",
        status="met",
        criteria=[CriterionResult(id="AC1", passed=True, evidence="implemented", checked=False)],
        cost_usd=1.25,
        tokens=1000,
    )


def _state() -> dict:
    critique = Critique(
        created_by="overseer",
        run_id="r",
        milestone_id="M1",
        point="design",
        items=[CritiqueItem(id="C1", severity="high", text="use float64", evidence=["E1"])],
    )
    return {
        "roadmap": roadmap(),
        "spec": milestone_spec(),
        "math_spec": math_spec(),
        "digest": _digest(),
        "design_critique": critique,
        "design_responses": [CritiqueResponse(id="C1", verdict="changed", text="done")],
    }


def test_export_writes_code_and_summary(ws: Workspace, tmp_path: Path) -> None:
    out = tmp_path / "export"
    folder = export_milestone(out, ws, _state())
    assert folder == out / "M1_milestone-1"
    assert (folder / "code/src/pkg/core.py").read_text() == "def f():\n    return 1\n"
    assert (folder / "code/results/table.json").is_file()
    assert not list(folder.rglob("__pycache__"))
    md = (folder / "summary.md").read_text()
    for text in (
        "# M1 · milestone 1",
        "AC1",
        "Math spec",
        "Result · M1",
        "Design review",
        "use float64",
        "changed: done",
        "$1.25",
    ):
        assert text in md, text
    assert "Roadmap v1" in (out / "roadmap.md").read_text()


def test_re_export_replaces_the_folder(ws: Workspace, tmp_path: Path) -> None:
    out = tmp_path / "export"
    folder = export_milestone(out, ws, _state())
    (folder / "code/stale.txt").write_text("old")
    export_milestone(out, ws, _state())
    assert not (folder / "code/stale.txt").exists()


def test_exports_committed_main_not_uncommitted_files(ws: Workspace, tmp_path: Path) -> None:
    (ws.root / "src/pkg/scratch.py").write_text("x = 1\n")  # never committed
    folder = export_milestone(tmp_path / "export", ws, _state())
    assert not (folder / "code/src/pkg/scratch.py").exists()


def test_slug_is_safe() -> None:
    assert slug("M3", "Baselines B1\u2013B5 & ../statistics!") == "M3_baselines-b1-b5-statistics"
    assert len(slug("M3", "x" * 300)) <= 64


def test_binary_results_are_exported_intact(ws: Workspace, tmp_path: Path) -> None:
    blob = bytes(range(256)) * 40
    (ws.root / "results/episodes.pt").write_bytes(blob)
    Git(ws).commit("tensors")
    folder = export_milestone(tmp_path / "export", ws, _state())
    assert (folder / "code/results/episodes.pt").read_bytes() == blob
