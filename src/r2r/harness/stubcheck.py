"""D93: the coder's finish line. Plain Python, no LLM: every stub the math designer wrote is still
there with the same parameters and is implemented, the package imports (in the sandbox), and a
milestone with benchmarks has a reproduce script that compiles. Correctness is the owner's and
the benchmarks' business, not this check's."""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from typing import Any

from r2r.contracts import ContractTooLarge, Failure, TestResult
from r2r.harness.sandbox import SandboxBackend
from r2r.harness.workspace import Workspace

Stub = dict[str, Any]  # {"path": "src/pkg/mod.py", "name": "Cls.method", "params": [...]}
MAX_FAILURES = 10


def reproduce_script(milestone_id: str) -> str:
    return f"bench/reproduce_{milestone_id.lower()}.py"


def _defs(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    out: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    out[f"{node.name}.{item.name}"] = item
    return out


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    a = fn.args
    names = [x.arg for x in (*a.posonlyargs, *a.args)]
    if a.vararg:
        names.append(f"*{a.vararg.arg}")
    names += [x.arg for x in a.kwonlyargs]
    if a.kwarg:
        names.append(f"**{a.kwarg.arg}")
    return names


def _is_stub(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]  # docstring
    if not body:
        return True
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # `...`
        if isinstance(stmt, ast.Raise) and "NotImplementedError" in ast.unparse(stmt):
            continue
        return False
    return True


def record_stubs(root: Path, paths: list[str]) -> list[Stub]:
    """The stub functions and methods in the designer's Python files under src/ and scripts/."""
    stubs: list[Stub] = []
    for rel in paths:
        if not (rel.startswith(("src/", "scripts/")) and rel.endswith(".py")):
            continue
        tree = ast.parse((root / rel).read_text())
        for name, fn in _defs(tree).items():
            if _is_stub(fn):
                stubs.append({"path": rel, "name": name, "params": _params(fn)})
    return stubs


def _modules(stubs: list[Stub]) -> list[str]:
    mods = []
    for s in stubs:
        if not s["path"].startswith("src/"):  # scripts are run, not imported as package modules
            continue
        parts = Path(s["path"]).with_suffix("").parts[1:]  # drop "src"
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        mod = ".".join(parts)
        if mod and mod not in mods:
            mods.append(mod)
    return mods


_IMPORT = """\
import importlib, json, sys, traceback
sys.path.insert(0, "src")
bad = {}
for m in json.loads(sys.argv[1]):
    try:
        importlib.import_module(m)
    except BaseException:
        bad[m] = traceback.format_exc()[-1500:]
sys.stdout.write("R2R-IMPORTS " + json.dumps(bad) + "\\n")
"""


def check_code(
    ws: Workspace,
    sandbox: SandboxBackend,
    stubs: list[Stub],
    reproduce: str | None,
    *,
    timeout_s: float,
    run_id: str,
    milestone_id: str,
) -> TestResult:
    start = time.monotonic()
    failures: list[Failure] = []
    n_ok = 0
    trees: dict[str, dict[str, Any] | None] = {}
    for s in stubs:
        path = ws.root / s["path"]
        if s["path"] not in trees:
            try:
                trees[s["path"]] = _defs(ast.parse(path.read_text())) if path.is_file() else None
            except SyntaxError as e:
                trees[s["path"]] = None
                failures.append(Failure(test_id=s["path"], message=f"syntax error: {e}"[:300]))
        defs = trees[s["path"]]
        fn = defs.get(s["name"]) if defs is not None else None
        tid = f"{s['path']}::{s['name']}"
        if fn is None:
            failures.append(Failure(test_id=tid, message=f"{s['name']} is missing"))
        elif _params(fn) != s["params"]:
            got = ", ".join(_params(fn))
            failures.append(
                Failure(
                    test_id=tid,
                    message=f"{s['name']}: parameters ({got}) differ from the stub's "
                    f"({', '.join(s['params'])})"[:300],
                )
            )
        elif _is_stub(fn):
            failures.append(Failure(test_id=tid, message=f"{s['name']} is not implemented yet"))
        else:
            n_ok += 1
    mods = _modules(stubs)
    if mods:
        res = sandbox.run(["python", "-c", _IMPORT, json.dumps(mods)], timeout_s)
        line = next((x for x in res.stdout.splitlines() if x.startswith("R2R-IMPORTS ")), None)
        if line is None:
            tail = (res.stderr or res.stdout)[-2000:]
            failures.append(
                Failure(test_id="import", message="import check did not run", tail=tail)
            )
        else:
            bad = json.loads(line.removeprefix("R2R-IMPORTS "))
            n_ok += len(mods) - len(bad)
            for m, tb in bad.items():
                failures.append(
                    Failure(test_id=f"import::{m}", message=f"import {m} fails", tail=tb[-2000:])
                )
    if reproduce is not None:
        path = ws.root / reproduce
        if not path.is_file():
            failures.append(
                Failure(
                    test_id=reproduce,
                    message=f"{reproduce} is missing: this milestone has benchmarks to reproduce",
                )
            )
        else:
            try:
                compile(path.read_text(), reproduce, "exec")
                n_ok += 1
            except SyntaxError as e:
                failures.append(Failure(test_id=reproduce, message=f"syntax error: {e}"[:300]))
    return _fit(
        failures,
        created_by="harness",
        run_id=run_id,
        milestone_id=milestone_id,
        passed=not failures,
        n_passed=n_ok,
        n_failed=len(failures),
        frozen_ok=True,
        duration_s=time.monotonic() - start,
    )


def _fit(failures: list[Failure], **fields: Any) -> TestResult:
    """The TestResult within its token cap: tracebacks shortened first, then fewer failures
    listed (the counts stay exact)."""
    shown = failures[:MAX_FAILURES]
    for tail in (2000, 600, 200, 0):
        trimmed = [f.model_copy(update={"tail": f.tail[-tail:] if tail else ""}) for f in shown]
        try:
            return TestResult(failures=trimmed, **fields)
        except ContractTooLarge:
            continue
    while shown:
        shown = shown[:-1]
        try:
            return TestResult(failures=[f.model_copy(update={"tail": ""}) for f in shown], **fields)
        except ContractTooLarge:
            continue
    return TestResult(failures=[], **fields)
