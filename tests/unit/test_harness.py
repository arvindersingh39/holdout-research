"""Harness without Docker: workspace, frozen hashes, tests, checks, bench, profile, gitops.

These run generated code as plain subprocesses (`LocalProcessBackend`); the isolation itself is
covered by `tests/docker/`.
"""

import shutil
from pathlib import Path

import pytest

from r2r.harness.bench import run_bench
from r2r.harness.checks import run_checks
from r2r.harness.frozen import FrozenTamper, NotFrozen, freeze, is_frozen, verify
from r2r.harness.gitops import Git, patch_paths
from r2r.harness.profile import run_profile
from r2r.harness.sandbox import LocalProcessBackend
from r2r.harness.tests import parse_junit, run_tests
from r2r.harness.workspace import UnsafePath, Workspace, safe_path
from r2r.store.runs import run_paths

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "slow_project"
META = {"run_id": "run-test", "milestone_id": "M1"}


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    for sub in ("src", "bench", "tests/frozen", "checks"):
        shutil.copytree(FIXTURE / sub, ws.root / sub, dirs_exist_ok=True)
    return ws


@pytest.fixture
def box(ws: Workspace) -> LocalProcessBackend:
    return LocalProcessBackend(ws)


# --- workspace ------------------------------------------------------------------------------


def test_workspace_layout_and_external_git(tmp_path: Path) -> None:
    paths = run_paths("r", base=tmp_path, create=True)
    ws = Workspace.create(paths)
    for d in ("src", "bench", "figures", "tests/frozen", "checks"):
        assert (ws.root / d).is_dir()
    assert (ws.root / "pyproject.toml").is_file()
    assert not (ws.root / ".git").exists()  # git dir lives outside the workspace
    assert ws.git_dir == paths.root / "workspace.git"
    assert Workspace.create(paths).root == ws.root  # idempotent
    assert Git(ws).head()  # initial commit exists


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "../outside.py",
        "src/../../x.py",
        "tests/frozen/test_x.py",
        "checks/c.py",
        "pyproject.toml",
    ],
)
def test_safe_path_rejects(ws: Workspace, bad: str) -> None:
    with pytest.raises(UnsafePath):
        safe_path(ws.root, bad, allowed=("src", "bench"))


def test_safe_path_rejects_escaping_symlink(ws: Workspace, tmp_path: Path) -> None:
    (ws.root / "src" / "link").symlink_to(tmp_path)
    with pytest.raises(UnsafePath):
        safe_path(ws.root, "src/link/evil.py", allowed=("src",))


def test_safe_path_accepts(ws: Workspace) -> None:
    assert safe_path(ws.root, "src/slowpkg/core.py", ("src",)) == ws.root / "src/slowpkg/core.py"
    assert safe_path(ws.root, "bench/new.py", ("src", "bench")).name == "new.py"


# --- frozen ---------------------------------------------------------------------------------


def test_freeze_and_verify(ws: Workspace) -> None:
    assert not is_frozen(ws)
    with pytest.raises(NotFrozen):
        verify(ws)
    digest = freeze(ws)
    assert len(digest) == 64 and is_frozen(ws)
    verify(ws)  # untouched: fine
    assert not (ws.root / "tests/frozen/test_core.py").stat().st_mode & 0o222  # read-only


@pytest.mark.parametrize("change", ["modify", "add", "remove"])
def test_tamper_detected(ws: Workspace, change: str) -> None:
    freeze(ws)
    target = ws.root / "tests/frozen/test_core.py"
    target.chmod(0o644)
    if change == "modify":
        target.write_text(target.read_text().replace("== 1", "== 0"))
    elif change == "add":
        (ws.root / "checks/check_new.py").write_text("print('sneaky')")
    else:
        target.unlink()
    with pytest.raises(FrozenTamper) as e:
        verify(ws)
    assert e.value.changed or e.value.added or e.value.removed


# --- tests ----------------------------------------------------------------------------------


def test_run_tests_maps_failures_to_criteria(ws: Workspace, box: LocalProcessBackend) -> None:
    freeze(ws)
    res = run_tests(ws, box, timeout_s=120, **META)
    assert res.created_by == "harness" and res.milestone_id == "M1"
    assert res.frozen_ok and not res.passed
    assert (res.n_passed, res.n_failed) == (6, 1)
    (f,) = res.failures
    assert f.criterion_id == "AC2"
    assert "test_AC2__deliberately_wrong" in f.test_id
    assert "fixture failure on purpose" in f.message
    assert len(f.tail.splitlines()) <= 15


def test_run_tests_requires_freeze(ws: Workspace, box: LocalProcessBackend) -> None:
    with pytest.raises(NotFrozen):
        run_tests(ws, box, timeout_s=60, **META)
    res = run_tests(ws, box, timeout_s=60, require_frozen=False, **META)
    assert res.n_failed == 1  # red-before-green runs happen before freezing


def test_run_tests_blocks_tampering(ws: Workspace, box: LocalProcessBackend) -> None:
    freeze(ws)
    p = ws.root / "tests/frozen/test_core.py"
    p.chmod(0o644)
    p.write_text(p.read_text().replace("== 1", "== 0"))
    with pytest.raises(FrozenTamper):
        run_tests(ws, box, timeout_s=60, **META)


def test_collection_error_becomes_failure(ws: Workspace, box: LocalProcessBackend) -> None:
    (ws.root / "src/slowpkg/core.py").write_text("def broken(:\n")
    freeze(ws)
    res = run_tests(ws, box, timeout_s=60, **META)
    assert not res.passed and res.n_failed >= 1
    assert res.failures and "SyntaxError" in (res.failures[0].message + res.failures[0].tail)


def test_parse_junit_truncates_and_caps() -> None:
    cases = (
        "".join(
            f'<testcase classname="tests.frozen.test_x" name="test_AC{i % 3 + 1}__case[{i}]">'
            f'<failure message="{"m" * 500}">{"line\n" * 40}</failure></testcase>'
            for i in range(25)
        )
        + '<testcase classname="t" name="test_ok" time="0.1"/>'
    )
    xml = f'<testsuites><testsuite name="pytest" tests="26">{cases}</testsuite></testsuites>'
    res = parse_junit(xml, frozen_ok=True, duration_s=1.0, **META)
    assert (res.n_passed, res.n_failed) == (1, 25)
    assert len(res.failures) == 10
    assert all(len(f.message) <= 300 and len(f.tail.splitlines()) <= 15 for f in res.failures)
    assert res.failures[0].criterion_id == "AC1"


def test_parse_junit_rejects_entities() -> None:
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><testsuites>&a;</testsuites>'
    res = parse_junit(evil, frozen_ok=True, duration_s=0.0, **META)
    assert not res.passed and "unsafe" in res.failures[0].message


# --- checks, bench, profile -----------------------------------------------------------------


def test_checks(ws: Workspace, box: LocalProcessBackend) -> None:
    (ws.root / "checks/check_bad.py").write_text("assert 1 == 2, 'identity broken'\n")
    results = run_checks(ws, box, timeout_s=60)
    assert {r.file: r.passed for r in results} == {
        "checks/check_bad.py": False,
        "checks/check_identity.py": True,
    }
    assert "identity broken" in next(r for r in results if not r.passed).output_tail


def test_bench(ws: Workspace, box: LocalProcessBackend) -> None:
    res = run_bench(ws, box, "bench/bench_main.py", repeats=3, timeout_s=120)
    assert res.median_s > 0 and len(res.times) >= 3 and res.cv >= 0


def test_bench_reruns_when_noisy(ws: Workspace, box: LocalProcessBackend) -> None:
    (ws.root / "bench/noisy.py").write_text(
        "import time\nn = 0\n\n\ndef main():\n    global n\n    n += 1\n"
        "    time.sleep(0.001 * (n % 2 * 20 + 1))\n"
    )
    res = run_bench(ws, box, "bench/noisy.py", repeats=3, timeout_s=60)
    assert res.cv > 0.10 and res.repeats == 9 and len(res.times) == 9


def test_profile_finds_slow_function(ws: Workspace, box: LocalProcessBackend) -> None:
    rep = run_profile(ws, box, "bench/bench_main.py", timeout_s=120, **META)
    assert rep.created_by == "harness"
    # Ranked by cumulative time (PLAN): `compute` includes the time of `slow_sum`, which it calls.
    by_name = {h.qualname: h for h in rep.hotspots}
    slow = by_name["slow_sum"]
    assert slow.file == "src/slowpkg/core.py"
    assert (slow.line_start, slow.line_end) == (4, 8)
    assert slow.pct > 50
    assert "closed_form" not in by_name or slow.pct > by_name["closed_form"].pct
    assert len(rep.hotspots) <= 5
    assert all(h.file.startswith("src/") for h in rep.hotspots)


# --- gitops ---------------------------------------------------------------------------------

PATCH = "\n".join(
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


def test_git_commit_diff_patch_revert(ws: Workspace) -> None:
    git = Git(ws)
    base = git.commit("fixture")
    assert patch_paths(PATCH) == ["src/slowpkg/core.py"]
    ok, reason = git.apply_patch(PATCH)
    assert ok, reason
    assert "for i in range" not in (ws.root / "src/slowpkg/core.py").read_text()
    assert "slow_sum" in git.diff(base)
    git.reset_hard(base)
    assert "for i in range" in (ws.root / "src/slowpkg/core.py").read_text()


def test_patch_outside_src_refused(ws: Workspace) -> None:
    git = Git(ws)
    git.commit("fixture")
    evil = PATCH.replace("src/slowpkg/core.py", "tests/frozen/test_core.py")
    ok, reason = git.apply_patch(evil)
    assert not ok and "not allowed" in reason


def test_malformed_patch_refused(ws: Workspace) -> None:
    git = Git(ws)
    git.commit("fixture")
    ok, reason = git.apply_patch("--- a/src/x.py\n+++ b/src/x.py\n@@ garbage @@\n+oops\n")
    assert not ok and reason


def test_branches(ws: Workspace) -> None:
    git = Git(ws)
    base = git.commit("fixture")
    git.branch("trial-1", base)
    assert git.current_branch() == "trial-1"
    git.checkout("main")
    assert git.current_branch() == "main"


def test_planted_hook_never_runs(ws: Workspace) -> None:
    git = Git(ws)
    hook_dir = ws.root / ".git" / "hooks"  # a workspace .git would be attacker-controlled
    hook_dir.mkdir(parents=True)
    marker = ws.root / "pwned"
    (hook_dir / "pre-commit").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (hook_dir / "pre-commit").chmod(0o755)
    git.commit("with a planted hook")
    assert not marker.exists()
