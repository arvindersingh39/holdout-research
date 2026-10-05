"""Phase 8 package mode: assemble, document by parsing (never importing), build command."""

import tomllib
from pathlib import Path

import pytest
from samples import math_spec, research_brief, result_digest, roadmap

from r2r.harness.workspace import Workspace
from r2r.publish.collect import MilestoneRecord, RunRecord
from r2r.publish.package import api_docs, assemble_package, build_wheel
from r2r.store.runs import run_paths

CORE = '''"""Ising helpers."""
import numpy as np
from scipy import stats
import os

open("IMPORTED", "w").write("generated code ran on the host")


def energy(spins: np.ndarray, J: float=1.0) -> float:
    """Total energy E = -J sum s_i s_j."""
    return 0.0


def _private():
    pass


class Lattice:
    """A periodic lattice."""

    def flip(self, i: int) -> None:
        """Flip one spin."""
'''


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    (ws.root / "src/ising").mkdir(parents=True)
    (ws.root / "src/ising/__init__.py").write_text("")
    (ws.root / "src/ising/core.py").write_text(CORE)
    (ws.root / "bench/reproduce_m1.py").write_text("print('reproduce')\n")
    return ws


def _record() -> RunRecord:
    m1 = roadmap().milestones[0]
    return RunRecord(
        roadmap(),
        research_brief(),
        [MilestoneRecord(m1, result_digest(), math_spec())],
        title="2D Ising Monte Carlo",
    )


def test_api_docs_parse_without_importing(ws: Workspace, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    md = api_docs(ws.root / "src")
    assert "energy(spins: np.ndarray, J: float=1.0) -> float" in md
    assert "Total energy E = -J sum s_i s_j." in md and "class Lattice" in md and "flip(" in md
    assert "_private" not in md
    assert not (tmp_path / "IMPORTED").exists() and not (ws.root / "IMPORTED").exists()


def test_assemble_package(ws: Workspace, tmp_path: Path) -> None:
    pkg = assemble_package(_record(), ws, tmp_path / "publish")
    assert pkg == tmp_path / "publish" / "package"
    meta = tomllib.loads((pkg / "pyproject.toml").read_text())
    assert meta["project"]["name"] == "ising"
    deps = meta["project"]["dependencies"]
    assert any(d.startswith("numpy") for d in deps) and any(d.startswith("scipy") for d in deps)
    assert not any(d.startswith("os") for d in deps)  # stdlib is not a dependency
    assert meta["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/ising"]
    assert (pkg / "src/ising/core.py").is_file() and (pkg / "bench/reproduce_m1.py").is_file()
    readme = (pkg / "README.md").read_text()
    assert "2D Ising Monte Carlo" in readme and "| M1 |" in readme and "reproduce" in readme
    assert "energy(" in (pkg / "API.md").read_text()
    assert "import ising" in (pkg / ".github/workflows/tests.yml").read_text()


def test_build_wheel_command(tmp_path: Path) -> None:
    calls = []

    def runner(cmd, cwd, env, timeout):
        calls.append((cmd, env))
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "ising-0.1.0-py3-none-any.whl").write_bytes(b"PK")
        return 0, "Successfully built"

    wheel, _ = build_wheel(tmp_path, runner=runner)
    cmd, env = calls[0]
    assert cmd[1:4] == ["build", "--wheel", "--out-dir"] and cmd[-1] == str(tmp_path)
    assert set(env) <= {"PATH", "HOME", "UV_CACHE_DIR"}  # no secrets reach the build
    assert wheel.name.endswith(".whl")
