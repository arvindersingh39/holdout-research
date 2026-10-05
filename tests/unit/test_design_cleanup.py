"""study-3: uncommitted stubs from rejected designs broke later designs. Before a design is
written, src/ and bench/ go back to the last commit; committed code is never rewritten."""

from pathlib import Path

from samples import math_design

from r2r.agents.math_designer import write_design
from r2r.contracts import MathDesign
from r2r.harness.workspace import Workspace
from r2r.store.runs import run_paths


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
    assert (root / "src/bs/core.py").is_file()  # the new design's own stub


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
