"""Phase 8 in the real sandbox: the arXiv-style paper compiles offline, with a figure (D66)."""

import json
import sys
from pathlib import Path

import pytest

from r2r.harness.sandbox import DockerBackend, ensure_image
from r2r.harness.workspace import Workspace
from r2r.llm.fake import FakeLLM
from r2r.llm.gateway import CallContext
from r2r.publish.paper import build_paper
from r2r.settings import load_settings
from r2r.store.ledger import Ledger
from r2r.store.runs import run_paths

sys.path.insert(0, str(Path(__file__).parents[1] / "unit"))
from samples import math_spec, paper_card, research_brief, result_digest, roadmap

from r2r.publish.collect import MilestoneRecord, RunRecord

pytestmark = pytest.mark.docker
REPO = Path(__file__).resolve().parents[2]
SETTINGS = load_settings(REPO / "config", env_file=None)
PLOT = (
    "import matplotlib; matplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\n"
    "fig, ax = plt.subplots(); ax.plot([1, 2, 3], [0.9, 0.95, 0.957])\n"
    "fig.savefig('figures/magnetization.png', dpi=72)\n"
)


def test_arxiv_paper_compiles_offline_with_figure(tmp_path: Path) -> None:
    image = ensure_image()
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    (ws.root / "figures" / "plot.py").write_text(PLOT)
    fake = FakeLLM()
    s = lambda n, t: json.dumps({"name": n, "latex": t})  # noqa: E731
    fake.script(
        "writer",
        s("abstract", r"We measure $|m|$ with 4 standard errors \& more."),
        s("introduction", r"Monte Carlo, see \cite{glasserman2003}."),
        s("related_work", r"\citet{glasserman2003} covers variance reduction."),
        s("method", r"\begin{equation} m = (1-\sinh(2/T)^{-4})^{1/8} \end{equation}"),
        s("experiments", r"Figure~\ref{fig:magnetization} shows runtime 0.41 s."),
        s("conclusion", "Done."),
    )
    ctx = CallContext(
        "run-test", None, SETTINGS, Ledger(tmp_path / "l.sqlite"), fake, REPO / "prompts"
    )
    ms = math_spec().model_copy(update={"figure_scripts": ["figures/plot.py"]})
    rec = RunRecord(
        roadmap(),
        research_brief(),
        [MilestoneRecord(roadmap().milestones[0], result_digest(), ms)],
        title="Offline compile check",
    )
    res = build_paper(
        rec,
        [paper_card()],
        ws,
        tmp_path / "publish",
        lambda w: DockerBackend(w, SETTINGS.budgets.sandbox, image=image),
        ctx,
    )
    assert res.build_ok, res.log_tail
    assert res.pdf.read_bytes()[:5] == b"%PDF-" and res.pdf.stat().st_size > 10_000
    assert res.figures == ["figures/magnetization.png"]


def test_wheel_installs_and_frozen_tests_pass_in_clean_sandbox(tmp_path: Path) -> None:
    from r2r.publish.package import assemble_package, build_wheel, verify_wheel

    image = ensure_image()
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    (ws.root / "src/sq").mkdir(parents=True)
    (ws.root / "src/sq/__init__.py").write_text("")
    (ws.root / "src/sq/core.py").write_text(
        "import numpy as np\n\n\ndef square(x):\n    return np.square(x)\n"
    )
    (ws.root / "tests/frozen/test_m1.py").write_text(
        "from sq.core import square\n\n\ndef test_AC1__square():\n    assert square(3) == 9\n"
    )
    rec = RunRecord(
        roadmap(),
        research_brief(),
        [MilestoneRecord(roadmap().milestones[0], result_digest(), math_spec())],
        title="Squares",
    )
    pkg = assemble_package(rec, ws, tmp_path / "publish")
    wheel, log = build_wheel(pkg)
    assert wheel is not None, log
    res = verify_wheel(
        wheel,
        pkg,
        lambda w: DockerBackend(w, SETTINGS.budgets.sandbox, image=image),
        tmp_path / "verify",
    )
    assert res.passed and res.n_passed == 1, res.log_tail
