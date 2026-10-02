"""Owner reference data (D71): small files in the proposal's data/ folder are snapshotted into the
run, installed read-only under tests/frozen/data/, and shown verbatim to the designer."""

import json
from pathlib import Path

import pytest

from r2r.harness.refdata import DATA_DIR, RefDataError, describe, install, snapshot
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


def test_install_into_frozen_dir_and_describe(tmp_path: Path) -> None:
    prop = _proposal(tmp_path, **{"recorded.json": '{"a": 1}'})
    paths = run_paths("run-test", base=tmp_path, create=True)
    ws = Workspace.create(paths)
    snapshot(prop, paths.root, LIM)
    rels = install(ws, paths.root)
    assert rels == [f"{DATA_DIR}/recorded.json"] and DATA_DIR == "tests/frozen/data"
    assert (ws.root / DATA_DIR / "recorded.json").read_text() == '{"a": 1}'
    assert install(ws, paths.root) == rels  # idempotent
    short = describe(paths.root, max_chars=5)[f"{DATA_DIR}/recorded.json"]
    assert short.startswith('{"a"') and "truncated" in short
    assert describe(paths.root, max_chars=1000) == {f"{DATA_DIR}/recorded.json": '{"a": 1}'}
