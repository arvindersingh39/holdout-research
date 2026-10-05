"""Owner reference data (D71): small files in the proposal's data/ folder are snapshotted into the
run, installed read-only under tests/frozen/data/, and shown verbatim to the designer."""

import json
from pathlib import Path

import pytest

from r2r.harness.refdata import RefDataError, snapshot
from r2r.harness.workspace import Workspace
from r2r.settings import load_settings
from r2r.store.runs import run_paths

REPO = Path(__file__).resolve().parents[2]
LIM = load_settings(REPO / "tests" / "fixtures" / "config", None).budgets.reference_data


def _proposal(tmp_path: Path, **files: str) -> Path:
    p = tmp_path / "proposal"
    (p / "data").mkdir(parents=True)
    (p / "proposal.md").write_text("# p")
    for name, text in files.items():
        (p / "data" / name).write_text(text)
    return p


def test_snapshot_copies_allowed_files_once(tmp_path: Path) -> None:
    prop = _proposal(
        tmp_path, **{"recorded.json": '{"kappa": [3.6]}', "notes.md": "n", "x.pkl": "b"}
    )
    run = tmp_path / "run"
    names = snapshot(prop, run, LIM)
    assert names == ["notes.md", "recorded.json"]  # .pkl is never read (pickles run code)
    (prop / "data" / "recorded.json").write_text('{"kappa": [9.9]}')  # edited after the run began
    assert snapshot(prop, run, LIM) == names
    assert json.loads((run / "data" / "recorded.json").read_text()) == {"kappa": [3.6]}


def test_no_data_folder_is_fine(tmp_path: Path) -> None:
    prop = tmp_path / "p"
    prop.mkdir()
    assert snapshot(prop, tmp_path / "run", LIM) == []


def test_oversized_file_fails_loudly(tmp_path: Path) -> None:
    prop = _proposal(tmp_path, **{"big.csv": "x" * (LIM.max_file_kb * 1024 + 1)})
    with pytest.raises(RefDataError, match=r"big\.csv"):
        snapshot(prop, tmp_path / "run", LIM)


def test_data_never_enters_the_workspace(tmp_path: Path) -> None:
    """D93: the coder sees benchmark refs only; the recorded values stay in the run folder."""
    prop = _proposal(tmp_path, **{"recorded.json": '{"a": 1}'})
    paths = run_paths("run-test", base=tmp_path, create=True)
    ws = Workspace.create(paths)
    snapshot(prop, paths.root, LIM)
    assert (paths.root / "data/recorded.json").is_file()
    assert not any(p.name == "recorded.json" for p in ws.root.rglob("*"))
