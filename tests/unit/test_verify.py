"""Phase 7d verifier (D75, D77, D78): reports from frozen verify scripts are checked against the
owner's reference file, using only the owner's tolerances. No LLM involved."""

import json
from pathlib import Path

import pytest

from r2r.harness.sandbox import LocalProcessBackend
from r2r.harness.verify import RefError, compare_entries, resolve, run_verify, tolerance_for
from r2r.harness.workspace import Workspace
from r2r.store.runs import run_paths

REF = {
    "ensemble": {
        "graphs": [
            {"n_stubs": 52, "mu": 1.0, "ok": True},
            {"n_stubs": 162, "mu": 3.05, "ok": False},
        ]
    },
    "graph0": {"B1": [0.001, 0.003]},
    "free_text": "hello",
    "loose": 0.5,
    "tolerances": {"ensemble.graphs[*].mu": 0.005, "graph0.B1[*]": 0.0005},
}


def test_resolve_paths() -> None:
    assert resolve("ensemble.graphs[1].n_stubs", REF) == 162
    assert resolve("graph0.B1[0]", REF) == 0.001
    with pytest.raises(RefError, match="graphs\\[7\\]"):
        resolve("ensemble.graphs[7].mu", REF)
    with pytest.raises(RefError):
        resolve("nope", REF)


def test_tolerance_patterns() -> None:
    tol = REF["tolerances"]
    assert tolerance_for("ensemble.graphs[1].mu", tol) == 0.005
    assert tolerance_for("graph0.B1[1]", tol) == 0.0005
    assert tolerance_for("ensemble.graphs[1].n_stubs", tol) is None


def test_compare_uses_owner_values_and_tolerances() -> None:
    entries = [
        {"ref": "ensemble.graphs[1].mu", "generated": 3.0526},  # within 0.005
        {
            "ref": "ensemble.graphs[0].mu",
            "generated": 1.2,
            "recorded": 1.2,
        },  # report's 'recorded' ignored
        {"ref": "ensemble.graphs[1].n_stubs", "generated": 162},  # int: exact
        {"ref": "ensemble.graphs[0].n_stubs", "generated": 53},  # int mismatch
        {"ref": "ensemble.graphs[1].ok", "generated": False},  # bool: exact
        {"ref": "loose", "generated": 0.51},  # float without tolerance: unchecked, never passed
        {"ref": "free_text", "generated": "hello"},  # string: exact
        {"ref": "missing.key", "generated": 1},  # bad ref: counted as an error
    ]
    c = compare_entries(entries, REF)
    assert (c.n_checked, c.n_within, c.n_mismatched, c.n_unchecked) == (6, 4, 2, 1)
    assert {m.ref for m in c.mismatches} == {"ensemble.graphs[0].mu", "ensemble.graphs[0].n_stubs"}
    assert any("missing.key" in e for e in c.errors)


SCRIPT_OK = """
import json, pathlib
from pkg.core import kappa

def main():
    out = pathlib.Path("results/verify/verify_ok.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    entries = [{"ref": "ensemble.graphs[1].mu", "generated": kappa()}]
    out.write_text(json.dumps({"entries": entries}))

if __name__ == "__main__":
    main()
"""


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    (ws.root / "src/pkg").mkdir(parents=True)
    (ws.root / "src/pkg/__init__.py").write_text("")
    (ws.root / "src/pkg/core.py").write_text("def kappa():\n    return 3.051\n")
    (ws.root / "tests/frozen/data").mkdir(parents=True, exist_ok=True)
    (ws.root / "tests/frozen/data/recorded_values.json").write_text(json.dumps(REF))
    (ws.root / "tests/frozen/verify").mkdir(parents=True)
    return ws


def _run(ws: Workspace):
    return run_verify(
        ws, LocalProcessBackend(ws), timeout_s=60, run_id="run-test", milestone_id="M1"
    )


def test_no_scripts_means_none(ws: Workspace) -> None:
    rep = _run(ws)
    assert rep.status == "none" and rep.scripts == []


def test_script_runs_and_is_verified(ws: Workspace) -> None:
    (ws.root / "tests/frozen/verify/verify_ok.py").write_text(SCRIPT_OK)
    rep = _run(ws)
    assert rep.status == "verified" and (rep.n_checked, rep.n_within) == (1, 1)


def test_mismatch_is_reported(ws: Workspace) -> None:
    (ws.root / "src/pkg/core.py").write_text("def kappa():\n    return 9.9\n")
    (ws.root / "tests/frozen/verify/verify_ok.py").write_text(SCRIPT_OK)
    rep = _run(ws)
    assert rep.status == "mismatch" and rep.mismatches[0].recorded == 3.05


def test_crashing_or_silent_script_is_an_error(ws: Workspace) -> None:
    (ws.root / "tests/frozen/verify/verify_boom.py").write_text("raise SystemExit(3)\n")
    (ws.root / "tests/frozen/verify/verify_quiet.py").write_text("print('no report')\n")
    rep = _run(ws)
    assert rep.status == "error"
    assert set(rep.failed_scripts) == {
        "tests/frozen/verify/verify_boom.py",
        "tests/frozen/verify/verify_quiet.py",
    }


def test_booleans_and_numbers_never_pass_as_each_other() -> None:
    ref = {"flag": True, "count": 1}
    c = compare_entries([{"ref": "flag", "generated": 1}, {"ref": "count", "generated": True}], ref)
    assert (c.n_checked, c.n_mismatched) == (2, 2)
