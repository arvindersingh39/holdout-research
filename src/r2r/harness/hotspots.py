"""Hotspot source for the optimizer: each profiled function's code by line range (Phase 6)."""

from __future__ import annotations

from r2r.contracts import ProfileReport
from r2r.harness.workspace import Workspace, safe_path

MAX_LINES = 300


def hotspot_source(ws: Workspace, report: ProfileReport, max_lines: int = MAX_LINES) -> str:
    """Source of each hotspot in profile order, labelled with file and line range. Ranges inside
    one already shown are skipped; the whole text is capped at `max_lines` lines of code."""
    shown: dict[str, list[tuple[int, int]]] = {}
    blocks: list[str] = []
    budget = max_lines
    for h in report.hotspots:
        if budget <= 0:
            break
        spans = shown.setdefault(h.file, [])
        if any(a <= h.line_start and h.line_end <= b for a, b in spans):
            continue
        try:
            lines = safe_path(ws.root, h.file, ("src",)).read_text().splitlines()
        except (OSError, ValueError):
            continue
        code = lines[h.line_start - 1 : h.line_end]
        header = f"# {h.file} lines {h.line_start}-{h.line_end} ({h.qualname})"
        if len(code) > budget:
            code = [*code[:budget], f"# ... truncated: {len(code) - budget} more lines"]
        blocks.append("\n".join([header, *code]))
        budget -= min(len(code), budget)
        spans.append((h.line_start, h.line_end))
    return "\n\n".join(blocks)
