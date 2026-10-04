"""study-3: test files from rejected designs stayed in tests/frozen/ and failed later designs.
Before a design is written, unfrozen leftovers in tests/frozen/ and checks/ are removed;
frozen files and the owner's reference data are kept."""

import json
from pathlib import Path

from samples import math_design

from r2r.agents.math_designer import write_design
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
    assert (root / "tests/frozen/test_bs.py").is_file()  # the new design's own test
