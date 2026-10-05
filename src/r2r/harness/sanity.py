"""Sanity checks (D83): the package's output must agree with an independent numerical oracle.

The math designer writes `tests/frozen/sanity/test_sanity_*.py`. Each file defines `oracle_*`
functions written from the equations alone (numpy/scipy/math) and `test_*` functions that compare
the package's public output with them on a tiny instance. Red-green and the reference check run
them like every frozen test; this module adds what those cannot see: the oracle must not call
the code it checks, every test must use both, and each file must be fast on the reference.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from pathlib import Path

from r2r.contracts import MathSpec
from r2r.harness.gitops import Git
from r2r.harness.tests import CaseOutcome
from r2r.harness.workspace import Workspace


def packages(src: Path) -> set[str]:
    """Top-level importable names under `src/` (packages and single modules)."""
    if not src.is_dir():
        return set()
    names = {p.name for p in src.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}
    return names | {p.stem for p in src.glob("*.py")}


def _top(module: str | None) -> str:
    return (module or "").split(".", 1)[0]


def _is_trusted(module: str, trusted: set[str]) -> bool:
    return module in trusted


def _bound_by_package(
    tree: ast.Module, pkgs: set[str], trusted: set[str] = frozenset()
) -> tuple[set[str], set[str]]:
    """Module-level names bound to the package: (new code, trusted earlier modules, D92)."""
    new: set[str] = set()
    old: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                if _top(a.name) in pkgs:
                    if a.asname and _is_trusted(a.name, trusted):
                        old.add(a.asname)
                    else:
                        new.add(a.asname or _top(a.name))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and _top(node.module) in pkgs:
            for a in node.names:
                name = a.asname or a.name
                if _is_trusted(node.module, trusted) or _is_trusted(
                    f"{node.module}.{a.name}", trusted
                ):
                    old.add(name)
                else:
                    new.add(name)
    return new, old


def _names(fn: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}


def _imports_package(fn: ast.AST, pkgs: set[str], trusted: set[str] = frozenset()) -> bool:
    """An import inside `fn` of package code other than trusted earlier modules."""
    for n in ast.walk(fn):
        if isinstance(n, ast.Import) and any(
            _top(a.name) in pkgs and not _is_trusted(a.name, trusted) for a in n.names
        ):
            return True
        if (
            isinstance(n, ast.ImportFrom)
            and n.level == 0
            and _top(n.module) in pkgs
            and not _is_trusted(n.module, trusted)
        ):
            return True
    return False


def lint_sanity(text: str, pkgs: set[str], trusted: set[str] = frozenset()) -> list[str]:
    """Problems with one sanity file; empty when the oracle is independent of the code it checks.
    D92: modules in `trusted` (verified by earlier milestones) may serve as oracles; the
    milestone's new code never may, and every check must call it."""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [f"syntax error: {e.msg} (line {e.lineno})"]
    pkg_names, _trusted_names = _bound_by_package(tree, pkgs, trusted)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    problems = []
    if not pkg_names:
        what = "this milestone's new code" if _trusted_names else "the package"
        problems.append(f"never imports {what} ({', '.join(sorted(pkgs))}) it checks")
    # helpers that touch the package (directly or through another helper) taint their callers
    tainted = {name for name, fn in funcs.items() if _names(fn) & pkg_names}
    grew = True
    while grew:
        grew = False
        for name, fn in funcs.items():
            if name not in tainted and _names(fn) & tainted:
                tainted.add(name)
                grew = True
    oracles = [n for n in funcs if n.startswith("oracle_")]
    if not oracles:
        problems.append("no oracle_* function: write the independent solution from the equations")
    for name in oracles:
        fn = funcs[name]
        used = sorted((_names(fn) & pkg_names) | ((_names(fn) & tainted) - {name}))
        if used:
            problems.append(
                f"{name} uses {', '.join(used)} from the package: an oracle must "
                "be independent of the code it checks"
            )
        if _imports_package(fn, pkgs, trusted):
            problems.append(f"{name} imports the package: an oracle must be independent of it")
    tests = [n for n in funcs if n.startswith("test_")]
    if not tests:
        problems.append("no test_* functions")
    for name in tests:
        seen = _names(funcs[name])
        if not (seen & set(oracles)) or not (seen & (pkg_names | tainted)):
            problems.append(f"{name} must compare the package's output with an oracle_* result")
    return problems


def trusted_modules(ws: Workspace, written: Iterable[str]) -> set[str]:
    """D92: modules committed by earlier milestones that this design does not write: verified by
    their own frozen checks, so they may serve as oracles for wrappers and refactors."""
    tracked = Git(ws).run("ls-files", "--", "src").split()
    touched = set(written)
    return {
        p.removeprefix("src/").removesuffix(".py").replace("/", ".")
        for p in tracked
        if p.endswith(".py") and not p.endswith("__init__.py") and p not in touched
    }


SKIPPED = "no sanity or contract check: skipped, the owner judges it"


def skip_unchecked(spec: MathSpec, criteria: Sequence[str]) -> MathSpec:
    """D90: criteria the design gives no sanity or contract check are skipped, not rejected:
    they join `unchecked_criteria` (the designer's own reasons stay) for the owner to judge."""
    covered = {cid for c in [*spec.sanity_checks, *spec.contract_checks] for cid in c.criterion_ids}
    missing = {cid: SKIPPED for cid in criteria if cid not in covered}
    return spec.model_copy(update={"unchecked_criteria": missing | dict(spec.unchecked_criteria)})


def check_sanity(
    spec: MathSpec,
    root: Path,
    reference_cases: Sequence[CaseOutcome] | None,
    pkgs: Iterable[str],
    *,
    criteria: Sequence[str],
    contract_file: str | None = None,
    trusted: set[str] = frozenset(),
) -> list[str]:
    """Design-time problems with the milestone's checks: unknown criteria, lint, presence and
    speed on the reference solution. Criteria without a check are skipped (`skip_unchecked`)."""
    problems = []
    named = {cid for c in [*spec.sanity_checks, *spec.contract_checks] for cid in c.criterion_ids}
    unknown = sorted(named - set(criteria))
    if unknown:
        problems.append(f"checks name unknown criteria: {', '.join(unknown)}")
    by_file: dict[str, float] = {}
    for c in spec.sanity_checks:
        by_file[c.file] = by_file.get(c.file, 0.0) + c.max_seconds
    if contract_file and spec.contract_checks:  # generated (D88): timed, not linted
        by_file[contract_file] = sum(c.max_seconds for c in spec.contract_checks)
    for rel, limit in by_file.items():
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel} is missing")
            continue
        if rel != contract_file:
            problems += [f"{rel}: {p}" for p in lint_sanity(path.read_text(), set(pkgs), trusted)]
        if reference_cases:
            took = sum(c.seconds for c in reference_cases if c.file == rel)
            if took > limit:
                problems.append(
                    f"{rel} took {took:.1f} s on the reference solution; limit "
                    f"{limit:g} s. Use a smaller instance."
                )
    return problems
