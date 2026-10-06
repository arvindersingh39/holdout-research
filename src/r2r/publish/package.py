"""Package mode (Phase 8, D68; D93): an installable package with its reproduce scripts.

The wheel is built on the host with `uv build`; hatchling reads only the pyproject we write and
never imports the generated code. API docs come from parsing the source with `ast` for the same
reason. The wheel is then installed into a clean, offline sandbox and every module of the
installed copy, not `src/`, must import.
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from r2r.harness.sandbox import SandboxBackend
from r2r.harness.workspace import Workspace
from r2r.publish.collect import RunRecord

SANDBOX_REQUIREMENTS = Path(__file__).resolve().parents[3] / "sandbox" / "requirements.txt"
THIRD_PARTY = ("numpy", "scipy", "pandas", "numba", "sympy", "matplotlib", "torch")
_SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
CI = """\
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install .
      - run: python -c "{imports}"
"""

Runner = Callable[[list[str], Path, dict[str, str], float], tuple[int, str]]


@dataclass(frozen=True)
class WheelCheck:
    passed: bool
    n_passed: int
    n_failed: int
    log_tail: str


def packages(src: Path) -> list[str]:
    return sorted(p.name for p in src.iterdir() if (p / "__init__.py").is_file())


def assemble_package(record: RunRecord, ws: Workspace, out_dir: Path) -> Path:
    pkg_dir = out_dir / "package"
    shutil.rmtree(pkg_dir, ignore_errors=True)
    shutil.copytree(ws.root / "src", pkg_dir / "src", ignore=_SKIP)
    if (ws.root / "bench").is_dir():  # reproduce scripts (D93)
        shutil.copytree(ws.root / "bench", pkg_dir / "bench", ignore=_SKIP)
    names = packages(pkg_dir / "src")
    if not names:
        raise ValueError("no Python package under src/")
    title = record.title or record.roadmap.goal
    (pkg_dir / "pyproject.toml").write_text(
        _pyproject(names, title, _dependencies(pkg_dir / "src"))
    )
    (pkg_dir / "README.md").write_text(_readme(record, names[0], title))
    (pkg_dir / "API.md").write_text(api_docs(pkg_dir / "src"))
    wf = pkg_dir / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "tests.yml").write_text(CI.format(imports="; ".join(f"import {n}" for n in names)))
    return pkg_dir


def build_wheel(pkg_dir: Path, runner: Runner | None = None) -> tuple[Path | None, str]:
    uv = shutil.which("uv") or "uv"
    env = {k: os.environ[k] for k in ("PATH", "HOME", "UV_CACHE_DIR") if k in os.environ}
    dist = pkg_dir / "dist"
    cmd = [uv, "build", "--wheel", "--out-dir", str(dist), str(pkg_dir)]
    code, out = (runner or _run)(cmd, pkg_dir, env, 300.0)
    wheels = sorted(dist.glob("*.whl")) if dist.is_dir() else []
    return (wheels[-1] if code == 0 and wheels else None), out[-4000:]


_IMPORT_ALL = """\
import importlib, pkgutil, sys
sys.path.insert(0, "/tmp/site")
ok = bad = 0
for name in sys.argv[1:]:
    pkg = importlib.import_module(name)
    for m in [name] + [i.name for i in pkgutil.walk_packages(pkg.__path__, name + ".")]:
        try:
            importlib.import_module(m)
            ok += 1
        except Exception as e:
            bad += 1
            sys.stdout.write(f"FAILED import {m}: {type(e).__name__}: {e}\\n")
sys.stdout.write(f"{ok} imported, {bad} failed\\n")
sys.exit(1 if bad else 0)
"""


def verify_wheel(
    wheel: Path,
    pkg_dir: Path,
    sandbox_for: Callable[[Workspace], SandboxBackend],
    verify_dir: Path,
    timeout_s: float = 600,
) -> WheelCheck:
    """Install the wheel alone into a fresh offline sandbox and import every module from it."""
    shutil.rmtree(verify_dir, ignore_errors=True)
    (verify_dir / "dist").mkdir(parents=True)
    shutil.copy2(wheel, verify_dir / "dist" / wheel.name)
    box = sandbox_for(Workspace(verify_dir, verify_dir / ".nogit", verify_dir / ".frozen.json"))
    names = packages(pkg_dir / "src")
    install = f"pip install --quiet --no-deps --no-index --target /tmp/site dist/{wheel.name}"
    # one container run: /tmp (where the wheel is installed) is fresh in every run
    script = f'{install} && python -c "$0" "$@"'
    res = box.run(["sh", "-c", script, _IMPORT_ALL, *names], timeout_s)
    out = res.stdout + "\n" + res.stderr
    m = re.search(r"(\d+) imported, (\d+) failed", out)
    n_pass, n_fail = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    ok = res.exit_code == 0 and not res.timed_out and n_pass > 0 and n_fail == 0
    return WheelCheck(ok, n_pass, n_fail, "\n".join(out.strip().splitlines()[-40:]))


def api_docs(src: Path) -> str:
    """Public functions and classes with signatures and docstrings, by parsing only."""
    lines = ["# API", ""]
    for path in sorted(src.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(errors="replace"))
        mod = ".".join(path.relative_to(src).with_suffix("").parts).removesuffix(".__init__")
        public = [n for n in tree.body if _public(n)]
        if not public:
            continue
        lines += [f"## `{mod}`", ""]
        if doc := ast.get_docstring(tree):
            lines += [doc, ""]
        for node in public:
            lines += _describe(node, level=3)
    return "\n".join(lines).rstrip() + "\n"


def _public(node: ast.AST) -> bool:
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    return isinstance(node, kinds) and not node.name.startswith("_")


def _describe(node: ast.AST, level: int) -> list[str]:
    h = "#" * level
    if isinstance(node, ast.ClassDef):
        out = [f"{h} class {node.name}", ""]
        if doc := ast.get_docstring(node):
            out += [doc, ""]
        for child in node.body:
            if _public(child):
                out += _describe(child, level + 1)
        return out
    assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    out = [f"{h} `{node.name}({ast.unparse(node.args)}){ret}`", ""]
    if doc := ast.get_docstring(node):
        out += [doc, ""]
    return out


def _dependencies(src: Path) -> list[str]:
    used: set[str] = set()
    for path in src.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(errors="replace"))):
            if isinstance(node, ast.Import):
                used |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                used.add(node.module.split(".")[0])
    pins = _pins()
    return [f"{n}>={pins[n]}" if n in pins else n for n in THIRD_PARTY if n in used]


def _pins() -> dict[str, str]:
    """name -> major.minor of the sandbox pin, so the package asks for what was tested."""
    if not SANDBOX_REQUIREMENTS.is_file():
        return {}
    pins = {}
    for line in SANDBOX_REQUIREMENTS.read_text().splitlines():
        if m := re.match(r"^([A-Za-z0-9_-]+)==(\d+)\.(\d+)", line.strip()):
            pins[m.group(1).lower()] = f"{m.group(2)}.{m.group(3)}"
    return pins


def _pyproject(names: list[str], title: str, deps: list[str]) -> str:
    dep_lines = "".join(f'    "{d}",\n' for d in deps)
    pkgs = ", ".join(f'"src/{n}"' for n in names)
    desc = title.replace('"', "'")[:200]
    return (
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n\n'
        f'[project]\nname = "{names[0]}"\nversion = "0.1.0"\ndescription = "{desc}"\n'
        f'readme = "README.md"\nrequires-python = ">=3.12"\ndependencies = [\n{dep_lines}]\n\n'
        f"[tool.hatch.build.targets.wheel]\npackages = [{pkgs}]\n\n"
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    )


def _readme(record: RunRecord, name: str, title: str) -> str:
    rows = [
        f"| {m.milestone.id} | {m.milestone.title} | {m.digest.status} | "
        f"{sum(c.passed for c in m.digest.criteria)}/{len(m.digest.criteria)} | "
        + ", ".join(f"{k} = {v:g}" for k, v in m.digest.metrics.items())
        + " |"
        for m in record.milestones
    ]
    sigs = [f"- `{s}`" for m in record.milestones if m.math_spec for s in m.math_spec.signatures]
    partial = "\n> Partial: not every planned milestone is complete.\n" if record.partial else ""
    return "\n".join(
        [
            f"# {title}",
            partial,
            record.roadmap.goal,
            "",
            "## Install",
            "",
            "```bash",
            "pip install .",
            "```",
            "",
            "## Usage",
            "",
            f"```python\nimport {name}\n```",
            "",
            *sigs,
            "",
            "See [API.md](API.md) for every public function.",
            "",
            "## Results",
            "",
            "Each milestone was implemented from a written formulation and approved by the owner; "
            "where the sources report values, they were reproduced with `bench/reproduce_*.py` "
            "and compared within stated tolerances.",
            "",
            "| Milestone | Title | Status | Criteria passed | Metrics |",
            "|---|---|---|---|---|",
            *rows,
            "",
            "## Reproduce",
            "",
            "```bash",
            "pip install .",
            "python bench/reproduce_<milestone>.py",
            "```",
            "",
        ]
    )


def _run(cmd: list[str], cwd: Path, env: dict[str, str], timeout: float) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, p.stdout + p.stderr
