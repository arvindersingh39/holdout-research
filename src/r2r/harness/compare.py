"""Comparison of reported values with the reference data (D75, D84; D93). Plain code; no LLM
decides anything here.

Reported values are `{"entries": [{"ref": "<path into the reference>", "generated": <value>}]}`.
The recorded value is always read from the reference at `ref`, and the tolerance only from its
`"tolerances"` map (patterns with `[*]` for any index). Without a tolerance, integers, booleans and
strings must match exactly; other values are reported as unchecked, never as passed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from r2r.contracts import Mismatch

MAX_MISMATCHES = 20
COPY_MIN = 3  # this many decimal values all equal to the recorded ones: copied, not computed
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
    decimals = copies = 0
    for e in entries:
        ref, gen = str(e.get("ref", "")), e.get("generated")
        try:
            rec = resolve(ref, reference)
        except RefError as err:
            c.errors.append(str(err))
            continue
        if isinstance(rec, float) and _number(gen):
            decimals += 1
            copies += gen == rec
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
    if decimals >= COPY_MIN and copies == decimals:  # D84: computed values never all match
        c.errors.append(
            f"copied: all {decimals} decimal values equal the recorded values exactly; "
            "compute them with the package"
        )
    return c


def _scalar(x: Any) -> Any:
    return x if isinstance(x, float | int | bool | str) or x is None else str(x)[:200]
