"""The Docker sandbox itself: isolation, limits, and the full harness pipeline inside it.

Run with `uv run pytest -q -m docker` (Docker must be running).
"""

import shutil
from pathlib import Path

import pytest

from r2r.harness.sandbox import DockerBackend, ensure_image
from r2r.harness.stubcheck import check_code, record_stubs
from r2r.harness.workspace import Workspace
from r2r.settings import SandboxLimits, load_settings
from r2r.store.runs import run_paths

pytestmark = pytest.mark.docker

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "slow_project"
LIMITS = load_settings(REPO / "tests" / "fixtures" / "config", env_file=None).budgets.sandbox
META = {"run_id": "run-test", "milestone_id": "M1"}


@pytest.fixture(scope="session")
def image() -> str:
    return ensure_image()


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    ws = Workspace.create(run_paths("run-test", base=tmp_path, create=True))
    for sub in ("src", "bench", "tests/frozen", "checks"):
        shutil.copytree(FIXTURE / sub, ws.root / sub, dirs_exist_ok=True)
    return ws


@pytest.fixture
def box(ws: Workspace, image: str) -> DockerBackend:
    return DockerBackend(ws, LIMITS, image=image)


def py(code: str) -> list[str]:
    return ["python", "-c", code]


def test_network_is_off(box: DockerBackend) -> None:
    res = box.run(py("import socket; socket.create_connection(('1.1.1.1', 53), 3)"), 30)
    assert res.exit_code != 0
    assert "Network is unreachable" in res.stderr or "Errno" in res.stderr


def test_timeout_kills(box: DockerBackend) -> None:
    res = box.run(["sleep", "60"], timeout_s=3)
    assert res.timed_out
    assert res.duration_s < 30


def test_frozen_dirs_are_read_only(box: DockerBackend) -> None:
    res = box.run(py("open('tests/frozen/evil.py', 'w').write('x')"), 30)
    assert res.exit_code != 0 and "Read-only file system" in res.stderr
    res = box.run(py("open('checks/evil.py', 'w').write('x')"), 30)
    assert res.exit_code != 0
    assert box.run(py("open('src/ok.py', 'w').write('x = 1')"), 30).exit_code == 0


def test_root_filesystem_is_read_only(box: DockerBackend) -> None:
    res = box.run(py("open('/usr/local/evil', 'w')"), 30)
    assert res.exit_code != 0


def test_no_host_environment(box: DockerBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-leak")
    res = box.run(["env"], 30)
    assert res.exit_code == 0
    for leaked in ("ANTHROPIC", "OPENROUTER", "LANGFUSE", "GEMINI", "sk-ant-"):
        assert leaked not in res.stdout


def test_non_root(box: DockerBackend) -> None:
    res = box.run(["id", "-u"], 30)
    assert res.stdout.strip() == "1000"


def test_memory_limit(ws: Workspace, image: str) -> None:
    small = SandboxLimits(cpus=1, memory="256m", pids=64, test_timeout_s=60, bench_timeout_s=60)
    res = DockerBackend(ws, small, image=image).run(
        py("x = bytearray(512 * 1024 * 1024); print(len(x))"), 60
    )
    assert res.exit_code != 0  # OOM-killed (137) or MemoryError


def test_pid_limit(ws: Workspace, image: str) -> None:
    small = SandboxLimits(cpus=1, memory="256m", pids=32, test_timeout_s=60, bench_timeout_s=60)
    code = (
        "import subprocess\nps = []\n"
        "try:\n"
        "    for _ in range(100):\n"
        "        ps.append(subprocess.Popen(['sleep', '5']))\n"
        "    print('spawned', len(ps))\n"
        "except OSError as e:\n"
        "    print('blocked', len(ps)); raise SystemExit(3)\n"
    )
    res = DockerBackend(ws, small, image=image).run(py(code), 60)
    assert res.exit_code == 3 and "blocked" in res.stdout


def test_scientific_stack(ws: Workspace, box: DockerBackend) -> None:
    # numba's on-disk cache needs a real file and must work with a read-only root (cache in /tmp)
    (ws.root / "src" / "stack_check.py").write_text(
        "import numpy as np, scipy, pandas, sympy\n"
        "from numba import njit\n"
        "@njit(cache=True)\n"
        "def f(n):\n    s = 0.0\n    for i in range(n):\n        s += i\n    return s\n"
        "assert f(10) == 45.0\n"
        "x = sympy.symbols('x'); assert sympy.diff(x**2, x) == 2*x\n"
        "print('ok', np.__version__)\n"
    )
    res = box.run(["python", "src/stack_check.py"], 120)
    assert res.exit_code == 0, res.stderr
    assert res.stdout.startswith("ok")


def test_stub_check_in_container(ws: Workspace, box: DockerBackend) -> None:
    """D93: the coder's finish line runs its import check in the sandbox."""
    (ws.root / "src/sq").mkdir()
    (ws.root / "src/sq/__init__.py").write_text("")
    stub = "def square(x: float) -> float:\n    raise NotImplementedError\n"
    (ws.root / "src/sq/core.py").write_text(stub)
    stubs = record_stubs(ws.root, ["src/sq/__init__.py", "src/sq/core.py"])
    first = check_code(ws, box, stubs, None, timeout_s=60, **META)
    assert not first.passed and "not implemented" in first.failures[0].message
    (ws.root / "src/sq/core.py").write_text(
        "import numpy as np\n\n\ndef square(x: float) -> float:\n    return float(np.square(x))\n"
    )
    assert check_code(ws, box, stubs, None, timeout_s=60, **META).passed
    (ws.root / "src/sq/core.py").write_text(
        "import requests_nowhere\n\n\ndef square(x):\n    return x\n"
    )
    bad = check_code(ws, box, stubs, None, timeout_s=60, **META)
    assert not bad.passed and any("import sq.core fails" in f.message for f in bad.failures)


def test_matplotlib_headless_png(ws: Workspace, box: DockerBackend) -> None:
    # D57: figures are drawn with no display, a read-only root and the font cache in /tmp
    code = (
        "import matplotlib; matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(); ax.loglog([1, 10, 100], [1, 0.3, 0.1]); ax.set_xlabel('$N$')\n"
        "fig.savefig('figures/check.png', dpi=72)\n"
    )
    res = box.run(py(code), 120)
    assert res.exit_code == 0, res.stderr[-2000:]
    assert (ws.root / "figures" / "check.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_tectonic_offline(ws: Workspace, box: DockerBackend) -> None:
    (ws.root / "figures" / "paper.tex").write_text(
        "\\documentclass{article}\\usepackage{amsmath,booktabs,hyperref}\n"
        "\\begin{document}Offline: $e^{i\\pi}+1=0$.\\end{document}\n"
    )
    res = box.run(["tectonic", "--only-cached", "-o", "figures", "figures/paper.tex"], 180)
    assert res.exit_code == 0, res.stderr[-2000:]
    assert (ws.root / "figures" / "paper.pdf").is_file()


def test_cli_sandbox_check(image: str) -> None:
    from typer.testing import CliRunner

    import r2r.cli as cli

    result = CliRunner().invoke(cli.app, ["sandbox", "check"])
    assert result.exit_code == 0, result.output
    assert result.output.count("✓") == 4 and "OK" in result.output


def test_cpu_torch_saves_and_loads_tensors(ws: Workspace, box: DockerBackend) -> None:
    """Owner decision (D96): CPU-only PyTorch in the sandbox, so packages save RL training data
    as .pt files. Works offline, with a read-only root, as the non-root user."""
    code = (
        "import torch, numpy as np\n"
        "assert not torch.cuda.is_available() and torch.__version__.endswith('+cpu')\n"
        "data = {'A': torch.from_numpy(np.eye(3)), 'I': torch.zeros(181, 3, dtype=torch.float64)}\n"
        "torch.save(data, 'results/net.pt')\n"
        "back = torch.load('results/net.pt', weights_only=True)\n"
        "assert torch.equal(back['A'], data['A']) and back['I'].shape == (181, 3)\n"
        "print('ok', torch.__version__)\n"
    )
    (ws.root / "results").mkdir(exist_ok=True)
    res = box.run(py(code), 180)
    assert res.exit_code == 0, res.stderr[-2000:]
    assert res.stdout.startswith("ok 2.14.1+cpu")
    assert (ws.root / "results/net.pt").stat().st_size > 0
