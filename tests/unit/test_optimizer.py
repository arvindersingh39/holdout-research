"""Optimizer harness on slow_project: baseline, trial-branch patches, keep/revert (FakeLLM-free)."""

import shutil
from pathlib import Path

import pytest

from r2r.contracts import OptimizationPatch
from r2r.harness.frozen import freeze
from r2r.harness.gitops import Git
from r2r.harness.optimize import Baseline, baseline, source_listing, try_patch
from r2r.harness.sandbox import LocalProcessBackend
from r2r.harness.workspace import Workspace
from r2r.settings import load_settings
from r2r.store.runs import run_paths

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "slow_project"
CFG = load_settings(REPO / "tests" / "fixtures" / "config", env_file=None).budgets
META = {"run_id": "run-test", "milestone_id": "M1"}
ENTRY = "bench/bench_main.py"


def _patch(diff: str, technique: str = "algorithmic") -> OptimizationPatch:
    return OptimizationPatch(
        created_by="optimizer", diff=diff, technique=technique, rationale="test patch", **META
    )


SPEEDUP = "\n".join(
    [
        "--- a/src/slowpkg/core.py",
        "+++ b/src/slowpkg/core.py",
        "@@ -4,8 +4,5 @@",
        " def slow_sum(n: int) -> int:",
        "-    total = 0",
        "-    for i in range(n):",
        "-        total += i * i",
        "-    return total",
        "+    return (n - 1) * n * (2 * n - 1) // 6",
        " ",
        " ",
        " def closed_form(n: int) -> int:",
        "",
    ]
)
WRONG = SPEEDUP.replace("+    return (n - 1) * n * (2 * n - 1) // 6", "+    return 0")


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    """slow_project with its deliberately failing test removed, frozen and committed."""
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    for sub in ("src", "bench", "tests/frozen", "checks"):
        shutil.copytree(FIXTURE / sub, ws.root / sub, dirs_exist_ok=True)
    t = ws.root / "tests/frozen/test_core.py"
    t.write_text(t.read_text().split("def test_AC2__deliberately_wrong")[0])
    freeze(ws)
    Git(ws).commit("M1: frozen")
    return ws


@pytest.fixture
def box(ws: Workspace) -> LocalProcessBackend:
    return LocalProcessBackend(ws)


@pytest.fixture
def base(ws: Workspace, box: LocalProcessBackend) -> Baseline:
    return baseline(ws, box, ENTRY, CFG, **META)


def _try(ws, box, base, patch, attempt=1):
    return try_patch(ws, box, Git(ws), patch, base, attempt, ENTRY, CFG, **META)


def test_baseline(base: Baseline) -> None:
    assert base.runtime_s > 0 and base.sloc > 5 and len(base.bench_hash) == 64
    assert base.complexity > 5  # statements + calls under src/ (D85)


def test_simpler_patch_is_kept(ws: Workspace, box: LocalProcessBackend, base: Baseline) -> None:
    out = _try(ws, box, base, _patch(SPEEDUP, technique="simplify"))
    assert out.accepted, out.reason
    assert "simpler" in out.reason
    assert out.complexity_after < out.complexity_before == base.complexity
    git = Git(ws)
    assert git.current_branch() == "main"
    assert "for i in range" not in (ws.root / "src/slowpkg/core.py").read_text()
    assert "simplifier attempt 1" in git.run("log", "--format=%s", "-1")


def test_behaviour_change_is_reverted(
    ws: Workspace, box: LocalProcessBackend, base: Baseline
) -> None:
    head = Git(ws).head()
    out = _try(ws, box, base, _patch(WRONG))
    assert not out.accepted and "tests" in out.reason
    assert out.runtime_after_s is None
    assert Git(ws).head() == head and Git(ws).current_branch() == "main"
    assert "for i in range" in (ws.root / "src/slowpkg/core.py").read_text()


@pytest.mark.parametrize(
    ("diff", "why"),
    [
        ("--- a/src/slowpkg/core.py\n+++ b/src/slowpkg/core.py\n@@ garbage @@\n+x\n", "apply"),
        (SPEEDUP.replace("src/slowpkg/core.py", "bench/bench_main.py"), "not allowed"),
        (SPEEDUP.replace("src/slowpkg/core.py", "tests/frozen/test_core.py"), "not allowed"),
    ],
)
def test_bad_diffs_are_failed_attempts(ws, box, base, diff, why) -> None:
    out = _try(ws, box, base, _patch(diff))
    assert not out.accepted and why in out.reason
    assert Git(ws).current_branch() == "main"
    assert "for i in range" in (ws.root / "src/slowpkg/core.py").read_text()


def test_bench_change_voids_attempt(
    ws: Workspace, box: LocalProcessBackend, base: Baseline
) -> None:
    (ws.root / ENTRY).write_text("def main():\n    pass\n")  # a much 'faster' benchmark
    out = _try(ws, box, base, _patch(SPEEDUP))
    assert not out.accepted and "bench" in out.reason


def test_trial_branches_are_cleaned_up(
    ws: Workspace, box: LocalProcessBackend, base: Baseline
) -> None:
    _try(ws, box, base, _patch(WRONG), attempt=1)
    _try(ws, box, base, _patch(SPEEDUP), attempt=2)
    assert Git(ws).run("branch", "--list", "opt/*").strip() == ""


# --- source listing for the simplifier -----------------------------------------------------


def test_source_listing_largest_first_and_capped(ws: Workspace) -> None:
    (ws.root / "src/slowpkg/tiny.py").write_text("X = 1\n")
    text = source_listing(ws)
    assert text.index("src/slowpkg/core.py") < text.index("src/slowpkg/tiny.py")
    assert "def slow_sum" in text
    capped = source_listing(ws, max_chars=200)
    assert len(capped) <= 400 and "omitted" in capped
