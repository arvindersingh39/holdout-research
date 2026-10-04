"""study-3: test files from rejected designs stayed in tests/frozen/ and failed later designs.
Before a design is written, unfrozen leftovers in tests/frozen/ and checks/ are removed;
frozen files and the owner's reference data are kept."""

import json
from pathlib import Path

from samples import math_design

from r2r.agents.math_designer import write_design
from r2r.contracts import MathDesign
from r2r.harness.workspace import Workspace
from r2r.store.runs import run_paths


def test_unfrozen_leftovers_are_removed(tmp_path: Path) -> None:
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    root = ws.root
    files = {
        "tests/frozen/test_m1_kept.py": "def test_x(): pass\n",  # frozen by an earlier milestone
        "tests/frozen/test_m7_old_design.py": "def test_y(): pass\n",  # leftover
        "tests/frozen/verify/verify_old.py": "x = 1\n",  # leftover
        "checks/check_old.py": "assert True\n",  # leftover
        "tests/frozen/data/recorded_values.json": "{}",  # owner data (D71)
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    ws.frozen_manifest.write_text(json.dumps({"files": {"tests/frozen/test_m1_kept.py": "h"}}))
    write_design(ws, math_design(), previous=None)
    assert (root / "tests/frozen/test_m1_kept.py").is_file()
    assert (root / "tests/frozen/data/recorded_values.json").is_file()
    assert not (root / "tests/frozen/test_m7_old_design.py").exists()
    assert not (root / "tests/frozen/verify/verify_old.py").exists()
    assert not (root / "checks/check_old.py").exists()
    assert (root / "tests/frozen/sanity/test_sanity_bs.py").is_file()  # the new design's own test


def test_uncommitted_src_from_rejected_designs_is_reset(tmp_path: Path) -> None:
    """study-3 M7: a rejected design's stub __init__.py imported names a later design dropped, and
    a redesign rewrote M2's committed module; every test then failed to import."""
    from r2r.harness.gitops import Git

    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    root = ws.root
    (root / "src/pkg").mkdir(parents=True)
    (root / "src/pkg/m2.py").write_text("def done(): return 2\n")  # finished milestone
    Git(ws).commit("M2: coder session 1 (pass)")
    (root / "src/pkg/m2.py").write_text("def done(): raise NotImplementedError\n")  # rewritten
    (root / "src/pkg/__init__.py").write_text("from .gone import x\n")  # rejected design's stub
    (root / "bench/bench_old.py").write_text("x = 1\n")
    write_design(ws, math_design(), previous=None)
    assert (root / "src/pkg/m2.py").read_text() == "def done(): return 2\n"
    assert not (root / "src/pkg/__init__.py").exists()
    assert not (root / "bench/bench_old.py").exists()
    assert (root / "tests/frozen/sanity/test_sanity_bs.py").is_file()


def test_redesign_cannot_rewrite_committed_src(tmp_path: Path) -> None:
    import pytest
    from samples import math_spec

    from r2r.agents.math_designer import DesignError
    from r2r.harness.gitops import Git

    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    data = math_design().model_dump()
    data["files"].append({"path": "src/pkg/core.py", "purpose": "stub", "content": "x = 1\n"})
    design = MathDesign.model_validate(data)
    (ws.root / "src/pkg").mkdir(parents=True)
    (ws.root / "src/pkg/core.py").write_text("# earlier milestone's code\n")
    Git(ws).commit("M1: coder session 1 (pass)")
    with pytest.raises(DesignError, match="already exists"):
        write_design(ws, design, previous=math_spec())


def test_tolerance_names_fit_owner_reference_paths() -> None:
    """Leak 1: recorded-value paths are longer than 60 characters."""
    from samples import math_spec

    ref = "report_signal_regime_approximate.gap_over_uniform.B4_optimised_static"
    ms = math_spec().model_copy(update={}).model_dump()
    ms["tolerances"] = {ref: 0.05}
    from r2r.contracts import MathSpec

    assert MathSpec.model_validate(ms).tolerances[ref] == 0.05
