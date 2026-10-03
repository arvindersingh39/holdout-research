"""Phase 7d verifier (D75, D77, D78): run the frozen verify scripts in the sandbox and check what
they report against the owner's reference file. Plain code; no LLM decides anything here.

A verify script (`tests/frozen/verify/verify_<topic>.py`) writes `results/verify/<topic>.json`:
`{"entries": [{"ref": "<path into the reference file>", "generated": <value>}]}`. The recorded value
is always read from the owner's file at `ref`, and the tolerance only from that file's
`"tolerances"` map (patterns with `[*]` for any index). Without a tolerance, integers, booleans and
strings must match exactly; other values are reported as unchecked, never as passed.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from typing import Any

from r2r.contracts import Mismatch, VerificationReport
from r2r.harness.refdata import DATA_DIR
from r2r.harness.sandbox import SandboxBackend
from r2r.harness.workspace import Workspace

VERIFY_DIR = "tests/frozen/verify"
OUT_DIR = "results/verify"
MAX_MISMATCHES = 20
_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


class RefError(KeyError):
    pass


def resolve(ref: str, data: Any) -> Any:
    """`a.b[3].c` → data["a"]["b"][3]["c"]; RefError names the part that is missing."""
    cur, seen = data, ""
    for name, index in _TOKEN.findall(ref):
        part = name if name else f"[{index}]"
        try:
            cur = cur[name] if name else cur[int(index)]
        except (KeyError, IndexError, TypeError):
            raise RefError(f"{ref}: no {seen}{part}") from None
        seen += part if part.startswith("[") or not seen else f".{part}"
    return cur


def tolerance_for(ref: str, tolerances: dict[str, float]) -> float | None:
    for pattern, tol in tolerances.items():
        rx = re.escape(pattern).replace(r"\[\*\]", r"\[\d+\]")
        if re.fullmatch(rx, ref):
            return float(tol)
    return None


@dataclass
class Comparison:
    n_checked: int = 0
    n_within: int = 0
    n_mismatched: int = 0
    n_unchecked: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _number(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)


def compare_entries(entries: list[dict[str, Any]], reference: dict[str, Any]) -> Comparison:
    tolerances = reference.get("tolerances") or {}
    c = Comparison()
    for e in entries:
        ref, gen = str(e.get("ref", "")), e.get("generated")
        try:
            rec = resolve(ref, reference)
        except RefError as err:
            c.errors.append(str(err))
            continue
        tol = tolerance_for(ref, tolerances)
        if tol is not None and _number(rec) and _number(gen):
            ok = abs(gen - rec) <= tol + 1e-12
        elif isinstance(rec, bool):  # exact, and 1 must not pass as True
            ok = isinstance(gen, bool) and gen == rec
        elif isinstance(rec, int | str):  # exact, and True must not pass as 1
            ok = not isinstance(gen, bool) and gen == rec
        else:
            c.n_unchecked += 1
            continue
        c.n_checked += 1
        if ok:
            c.n_within += 1
        else:
            c.n_mismatched += 1
            if len(c.mismatches) < MAX_MISMATCHES:
                c.mismatches.append(
                    Mismatch(
                        ref=ref[:300], generated=_scalar(gen), recorded=_scalar(rec), tolerance=tol
                    )
                )
    return c


def _scalar(x: Any) -> Any:
    return x if isinstance(x, float | int | bool | str) or x is None else str(x)[:200]


def _reference(ws: Workspace) -> dict[str, Any]:
    """The owner's JSON reference data (D71), merged; later files win on clashing top-level keys."""
    merged: dict[str, Any] = {}
    for p in sorted((ws.root / DATA_DIR).glob("*.json")):
        data = json.loads(p.read_text())
        if isinstance(data, dict):
            tol = {**merged.get("tolerances", {}), **(data.get("tolerances") or {})}
            merged.update(data)
            merged["tolerances"] = tol
    return merged


def run_verify(
    ws: Workspace,
    sandbox: SandboxBackend,
    *,
    timeout_s: float,
    run_id: str,
    milestone_id: str,
) -> VerificationReport:
    meta = {"created_by": "harness", "run_id": run_id, "milestone_id": milestone_id}
    scripts = sorted((ws.root / VERIFY_DIR).glob("verify_*.py"))
    if not scripts:
        return VerificationReport(status="none", **meta)
    out_dir = ws.root / OUT_DIR
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    reference = _reference(ws)
    total, failed, logs = Comparison(), [], []
    for path in scripts:
        rel = path.relative_to(ws.root).as_posix()
        code = (
            "import runpy, sys; sys.path.insert(0, 'src'); "
            f"runpy.run_path({rel!r}, run_name='__main__')"
        )
        res = sandbox.run(["python", "-c", code], timeout_s)
        report = out_dir / f"{path.stem}.json"
        problem = None
        if res.timed_out or res.exit_code != 0:
            problem = f"exit {res.exit_code}{' (timed out)' if res.timed_out else ''}"
        elif not report.is_file():
            problem = f"wrote no {OUT_DIR}/{path.stem}.json"
        else:
            try:
                entries = json.loads(report.read_text())["entries"]
                assert isinstance(entries, list)
            except (ValueError, KeyError, TypeError, AssertionError):
                problem = "report is not {'entries': [...]} JSON"
        if problem:
            failed.append(rel)
            logs.append(f"{rel}: {problem}\n{(res.stderr or res.stdout)[-600:]}")
            continue
        c = compare_entries(entries, reference)
        total.n_checked += c.n_checked
        total.n_within += c.n_within
        total.n_mismatched += c.n_mismatched
        total.n_unchecked += c.n_unchecked
        total.mismatches += c.mismatches[: MAX_MISMATCHES - len(total.mismatches)]
        logs += [f"{rel}: bad ref {e}" for e in c.errors]
    if failed or any("bad ref" in line for line in logs):
        status = "error"
    elif total.n_mismatched:
        status = "mismatch"
    elif total.n_checked:
        status = "verified"
    else:
        status = "unchecked_only"
    tail = "\n".join("\n".join(logs).splitlines()[-40:])[-4000:]
    return VerificationReport(
        status=status,
        scripts=[p.relative_to(ws.root).as_posix() for p in scripts],
        failed_scripts=failed,
        n_checked=total.n_checked,
        n_within=total.n_within,
        n_mismatched=total.n_mismatched,
        n_unchecked=total.n_unchecked,
        mismatches=total.mismatches,
        log_tail=tail,
        **meta,
    )
