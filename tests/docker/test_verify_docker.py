"""Phase 7d in the real sandbox: a verify script runs in a fresh, offline container."""

import json
from pathlib import Path

import pytest

from r2r.harness.sandbox import DockerBackend, ensure_image
from r2r.harness.verify import run_verify
from r2r.harness.workspace import Workspace
from r2r.settings import load_settings
from r2r.store.runs import run_paths

pytestmark = pytest.mark.docker
REPO = Path(__file__).resolve().parents[2]
LIMITS = load_settings(REPO / "tests" / "fixtures" / "config", env_file=None).budgets.sandbox
SCRIPT = """import json, pathlib
import numpy as np
from pkg.core import mean_degree


def main():
    out = pathlib.Path("results/verify/verify_degree.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"entries": [{"ref": "mean_degree", "generated": mean_degree()}]}))


if __name__ == "__main__":
    main()
"""


def test_verify_script_runs_offline_in_the_sandbox(tmp_path: Path) -> None:
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    (ws.root / "src/pkg").mkdir(parents=True)
    (ws.root / "src/pkg/__init__.py").write_text("")
    (ws.root / "src/pkg/core.py").write_text(
        "import numpy as np\n\n\ndef mean_degree():\n    return float(np.mean([1, 2, 3, 4]))\n"
    )
    (ws.root / "tests/frozen/data").mkdir(parents=True, exist_ok=True)
    ref = {"mean_degree": 2.5, "tolerances": {"mean_degree": 1e-12}}
    (ws.root / "tests/frozen/data/recorded_values.json").write_text(json.dumps(ref))
    (ws.root / "tests/frozen/verify").mkdir(parents=True)
    (ws.root / "tests/frozen/verify/verify_degree.py").write_text(SCRIPT)
    box = DockerBackend(ws, LIMITS, image=ensure_image())
    rep = run_verify(ws, box, timeout_s=120, run_id="run-test", milestone_id="M1")
    assert rep.status == "verified", rep.log_tail
    assert (rep.n_checked, rep.n_within) == (1, 1)
