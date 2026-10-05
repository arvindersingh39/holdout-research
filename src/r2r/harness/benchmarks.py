"""D93: benchmarks, the values a milestone must reproduce. Plain code; no LLM decides here.

Two sources, one reference: the owner's recorded values (`<run>/data/*.json`, D71) and the
experimental results the retriever found in the proposal and papers, quote-checked here and stored
as `<run>/data/benchmarks.json`. The reference never enters the workspace: the coder sees refs and
descriptions only. The owner runs the milestone's reproduce script, which writes
`{"entries": [{"ref", "generated"}]}`; this module compares that file with the reference.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from r2r.contracts import Benchmark, VerificationReport
from r2r.harness.compare import MAX_MISMATCHES, RefError, compare_entries, resolve

DATA = "data"
BENCHMARKS_FILE = "benchmarks.json"
RESULTS_DIR = "results/benchmarks"
SKIP_KEYS = {"tolerances", "about"}
MAX_RESULTS_KB = 2048
_MINUS = "\u2212"  # the typographic minus sign in PDFs
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")


class BenchmarkFileError(ValueError):
    """The owner's results file is missing or not in the expected format."""


# --- from the retriever ---------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(text.replace(_MINUS, "-").split()).lower()


def _numbers(quote: str) -> list[float]:
    out = []
    for m in _NUMBER.finditer(quote.replace(_MINUS, "-")):
        try:
            x = float(m.group().replace(",", ""))
        except ValueError:
            continue
        out.append(x)
        if quote[m.end() : m.end() + 1] == "%":
            out.append(x / 100)
    return out


def verify_benchmarks(benchmarks: list[Benchmark], texts: dict[str, str]) -> list[Benchmark]:
    """`verified`: the quote occurs in its source text (any text when the source is unknown) and
    states the value. Unverified ones are kept, marked, for the owner to judge at the roadmap."""
    normed = {k: _norm(v) for k, v in texts.items()}
    out = []
    for b in benchmarks:
        pool = [normed[b.source]] if b.source in normed else list(normed.values())
        quoted = any(_norm(b.quote) in t for t in pool)
        tol = max(abs(b.value) * 1e-9, 1e-12)
        stated = any(abs(x - b.value) <= tol for x in _numbers(b.quote))
        out.append(b.model_copy(update={"verified": quoted and stated}))
    return out


def write_benchmarks(
    run_root: Path, benchmarks: list[Benchmark], keep_existing: bool = False
) -> list[Benchmark]:
    """Store the benchmarks as reference data. With `keep_existing` (a run extended with a new
    proposal), they are renumbered after the stored ones, which stay; returns what was added."""
    path = run_root / DATA / BENCHMARKS_FILE
    data: dict[str, Any] = {"benchmarks": {}, "tolerances": {}, "about": {}}
    if keep_existing and path.is_file():
        data = json.loads(path.read_text())
        start = max((int(k[2:]) for k in data["benchmarks"]), default=0)
        benchmarks = [
            b.model_copy(update={"id": f"BM{start + n}"}) for n, b in enumerate(benchmarks, 1)
        ]
    for b in benchmarks:
        data["benchmarks"][b.id] = b.value
        data["tolerances"][f"benchmarks.{b.id}"] = b.tolerance
        data["about"][b.id] = {
            "quantity": b.quantity,
            "units": b.units,
            "conditions": b.conditions,
            "source": b.source,
            "quote": b.quote,
            "verified": b.verified,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))
    return benchmarks


# --- the reference ---------------------------------------------------------------------------


def load_reference(run_root: Path) -> dict[str, Any]:
    """Every JSON file in the run's data folder, merged (later files win on clashing top-level
    keys; tolerances and descriptions are merged)."""
    merged: dict[str, Any] = {}
    for p in sorted((run_root / DATA).glob("*.json")):
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            continue
        tol = {**merged.get("tolerances", {}), **(data.get("tolerances") or {})}
        about = {**merged.get("about", {}), **(data.get("about") or {})}
        merged.update(data)
        merged["tolerances"], merged["about"] = tol, about
    return merged


def _leaves(node: Any, prefix: str) -> list[str]:
    if isinstance(node, dict):
        return [r for k, v in node.items() for r in _leaves(v, f"{prefix}.{k}" if prefix else k)]
    if isinstance(node, list):
        return [r for i, v in enumerate(node) for r in _leaves(v, f"{prefix}[{i}]")]
    return [prefix]


def expected_refs(reference: dict[str, Any], prefixes: list[str]) -> list[str]:
    """Every value under the milestone's benchmark refs (a ref may name a whole subtree)."""
    out: list[str] = []
    for p in prefixes:
        if p.split(".", 1)[0].split("[", 1)[0] in SKIP_KEYS:
            continue
        for r in _leaves(resolve(p, reference), p):
            if r not in out:
                out.append(r)
    return out


def coder_view(reference: dict[str, Any], prefixes: list[str], limit: int = 300) -> list[dict]:
    """What the coder may know: each ref and, for proposal benchmarks, what it measures. Never the
    value."""
    about = reference.get("about") or {}
    view = []
    for r in expected_refs(reference, prefixes)[:limit]:
        item: dict[str, str] = {"ref": r}
        if r.startswith("benchmarks."):
            a = about.get(r.removeprefix("benchmarks."), {})
            item |= {k: str(a.get(k, "")) for k in ("quantity", "units", "conditions")}
        view.append(item)
    return view


def outline(reference: dict[str, Any], max_lines: int = 80) -> str:
    """Refs the long planner may assign to milestones: each proposal benchmark, and the owner's
    data down to two levels with value counts."""
    lines = []
    about = reference.get("about") or {}
    for bid in reference.get("benchmarks") or {}:
        a = about.get(bid, {})
        mark = "" if a.get("verified", True) else " (quote not verified)"
        lines.append(f"benchmarks.{bid}: {a.get('quantity', '')} [{a.get('units', '')}]{mark}")
    for key, node in reference.items():
        if key in SKIP_KEYS or key == "benchmarks":
            continue
        children = node.items() if isinstance(node, dict) else [("", node)]
        for sub, child in children:
            ref = f"{key}.{sub}" if sub else key
            lines.append(f"{ref}: {len(_leaves(child, ref))} value(s)")
    if len(lines) > max_lines:
        lines = [*lines[:max_lines], f"... {len(lines) - max_lines} more"]
    return "\n".join(lines)


# --- the owner's results -----------------------------------------------------------------------


def default_results(milestone_id: str) -> str:
    return f"{RESULTS_DIR}/{milestone_id.lower()}.json"


def results_file(ws_root: Path, milestone_id: str, note: str) -> Path:
    """The file the owner points to (relative paths are inside the workspace); the default when
    the note is empty."""
    raw = note.strip() or default_results(milestone_id)
    p = Path(raw).expanduser()
    if p.suffix.lower() != ".json":
        raise BenchmarkFileError(f"{raw}: the results file must be .json")
    return p if p.is_absolute() else ws_root / p


def check_results(
    path: Path,
    reference: dict[str, Any],
    prefixes: list[str],
    *,
    run_id: str,
    milestone_id: str,
) -> VerificationReport:
    if not path.is_file():
        raise BenchmarkFileError(f"{path} not found: run the reproduce script first")
    if path.stat().st_size > MAX_RESULTS_KB * 1024:
        raise BenchmarkFileError(f"{path} is over {MAX_RESULTS_KB} KB")
    try:
        entries = json.loads(path.read_text())["entries"]
        assert isinstance(entries, list) and all(isinstance(e, dict) for e in entries)
    except (ValueError, KeyError, TypeError, AssertionError):
        raise BenchmarkFileError(
            f'{path} is not {{"entries": [{{"ref": ..., "generated": ...}}]}} JSON'
        ) from None
    problems: list[str] = []
    try:
        expected = expected_refs(reference, prefixes)
    except RefError as e:
        expected, problems = [], [f"benchmark ref not in the reference data: {e}"]
    reported = {str(e.get("ref", "")) for e in entries}
    missing = [r for r in expected if r not in reported]
    if missing:
        shown = ", ".join(missing[:10]) + (" …" if len(missing) > 10 else "")
        problems.append(f"{len(missing)} benchmark value(s) not reported: {shown}")
    mine = [e for e in entries if str(e.get("ref", "")) in set(expected)]
    c = compare_entries(mine, reference)
    problems += c.errors
    if problems:
        status = "error"
    elif c.n_mismatched:
        status = "mismatch"
    elif c.n_checked:
        status = "verified"
    else:
        status = "unchecked_only"
    return VerificationReport(
        created_by="harness",
        run_id=run_id,
        milestone_id=milestone_id,
        status=status,
        scripts=[str(path)[-200:]],
        n_checked=c.n_checked,
        n_within=c.n_within,
        n_mismatched=c.n_mismatched,
        n_unchecked=c.n_unchecked,
        mismatches=c.mismatches[:MAX_MISMATCHES],
        log_tail="\n".join(problems)[-4000:],
    )
