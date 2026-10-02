"""Build the paper (Phase 8): figures, one writer call per section, code checks, offline compile
with tectonic in the sandbox, and one fix pass if LaTeX fails."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from r2r.agents.writer import write_section
from r2r.contracts import BaselineCard, PaperCard
from r2r.harness.sandbox import SandboxBackend
from r2r.harness.workspace import FROZEN_DIRS, Workspace
from r2r.ingest.documents import wrap_document
from r2r.llm.gateway import CallContext
from r2r.log import get_logger
from r2r.publish.collect import RunRecord
from r2r.publish.latex import (
    baseline_keys,
    bib_entries,
    bibliography,
    figure_label,
    render_paper,
    unverified_numbers,
)

log = get_logger("r2r.publish.paper")
ORDER = ("abstract", "introduction", "related_work", "method", "experiments", "conclusion")
FIGURE_EXTS = (".png", ".pdf", ".jpg", ".jpeg")
_ERROR_LINE = re.compile(r"paper\.tex:(\d+):")


@dataclass
class PaperResult:
    build_ok: bool
    pdf: Path | None
    tex: Path
    log_tail: str
    sections: list[str]
    figures: list[str]
    unverified: dict[str, list[str]] = field(default_factory=dict)


def build_paper(
    record: RunRecord,
    cards: Sequence[PaperCard],
    ws: Workspace,
    out_dir: Path,
    sandbox_for: Callable[[Workspace], SandboxBackend],
    ctx: CallContext,
    *,
    note: str | None = None,
) -> PaperResult:
    lim = ctx.settings.budgets.publish
    paper_dir = out_dir / "paper"
    shutil.rmtree(paper_dir, ignore_errors=True)
    (paper_dir / "figures").mkdir(parents=True)
    figures = _figures(record, ws, sandbox_for, lim.compile_timeout_s, paper_dir)
    baselines = [b for m in record.milestones for b in m.baselines]
    entries = bib_entries(cards, baselines)
    keys = {c.citation.key: c.citation.title for c in cards}
    keys |= {k: u for u, k in baseline_keys(baselines).items()}
    keys = {e.key: keys.get(e.key, e.key) for e in entries}
    inputs = _inputs(record, cards, baselines, figures)
    title = record.title or record.roadmap.goal
    labels = [figure_label(p) for p, _ in figures]

    def write(name: str, **kw) -> str:
        data, _ = inputs[name]
        return write_section(
            name,
            title,
            wrap_document(data, source=name),
            keys,
            ctx,
            partial=record.partial,
            figures=labels,
            note=note,
            **kw,
        ).latex

    sections = {name: write(name) for name in ORDER if name in inputs}
    build = Workspace(paper_dir, paper_dir / ".nogit", paper_dir / ".frozen.json")
    tex_path, pdf, log_tail = paper_dir / "paper.tex", paper_dir / "paper.pdf", ""
    for attempt in range(lim.fix_attempts + 1):
        tex = render_paper(
            title=title,
            authors=["r2r research pipeline"],
            sections=sections,
            figures=figures,
            bib=bibliography(entries),
            partial=record.partial,
        )
        tex_path.write_text(tex)
        ok, log_tail = _compile(build, sandbox_for, lim.compile_timeout_s, lim.log_tail_lines)
        if ok or attempt == lim.fix_attempts:
            break
        bad = _section_at(tex, sections, log_tail)
        if bad is None:
            break  # the error is in the template or bibliography: report it
        sections[bad] = write(bad, previous=sections[bad], compile_error=log_tail)
    unverified = {
        name: found
        for name, text in sections.items()
        if name != "related_work" and (found := unverified_numbers(text, inputs[name][1]))
    }
    return PaperResult(
        build_ok=ok and pdf.is_file(),
        pdf=pdf if ok and pdf.is_file() else None,
        tex=tex_path,
        log_tail=log_tail,
        sections=list(sections),
        figures=[p for p, _ in figures],
        unverified=unverified,
    )


def _figures(record, ws, sandbox_for, timeout_s, paper_dir) -> list[tuple[str, str]]:
    """Run each MathSpec's figure scripts in the sandbox, then collect every figure file."""
    scripts = [s for m in record.milestones if m.math_spec for s in m.math_spec.figure_scripts]
    if scripts:
        box = sandbox_for(ws)
        for script in scripts:
            res = box.run(["python", script], timeout_s)
            if res.exit_code != 0:
                log.warning("figure script failed", extra={"script": script})
    found = []
    for f in sorted((ws.root / "figures").glob("*")):
        if f.suffix.lower() in FIGURE_EXTS and f.is_file():
            shutil.copy2(f, paper_dir / "figures" / f.name)
            found.append((f"figures/{f.name}", f.stem.replace("_", " ").capitalize() + "."))
    return found


def _compile(build: Workspace, sandbox_for, timeout_s: int, tail_lines: int) -> tuple[bool, str]:
    made = [build.root / d for d in FROZEN_DIRS if not (build.root / d).exists()]
    for d in made:
        d.mkdir(parents=True)  # the sandbox mounts these read-only
    try:
        cmd = ["tectonic", "--only-cached", "-o", ".", "paper.tex"]
        res = sandbox_for(build).run(cmd, timeout_s)
    finally:
        for d in made:
            shutil.rmtree(d, ignore_errors=True)
        if (build.root / "tests").exists() and not any((build.root / "tests").iterdir()):
            (build.root / "tests").rmdir()
    text = "\n".join((res.stdout + "\n" + res.stderr).strip().splitlines()[-tail_lines:])
    return res.exit_code == 0 and not res.timed_out, text


def _section_at(tex: str, sections: dict[str, str], log_tail: str) -> str | None:
    """The section whose text contains the line the compiler complained about."""
    m = _ERROR_LINE.search(log_tail)
    if not m:
        return None
    line = int(m.group(1))
    for name, body in sections.items():
        start = tex.find(body)
        if start >= 0:
            first = tex[:start].count("\n") + 1
            if first <= line <= first + body.count("\n"):
                return name
    return None


def _inputs(
    record: RunRecord,
    cards: Sequence[PaperCard],
    baselines: list[BaselineCard],
    figures: list[tuple[str, str]],
) -> dict[str, tuple[str, list[str]]]:
    """Section -> (input text, verified number sources). Planned criterion wording is shown
    for context but never counts as a source of numbers: plans can be wrong, test results aren't."""
    b = record.brief
    brief = b.model_dump_json(exclude={"paper_cards", "created_by", "run_id"}) if b else ""
    ms = record.milestones
    keys = baseline_keys(baselines)
    results = [
        f"{m.milestone.id} {m.milestone.title}: status {m.digest.status}; "
        + "; ".join(
            f"{c.id} {'passed' if c.passed else 'FAILED'} ({c.evidence})" for c in m.digest.criteria
        )
        + f"; metrics {m.digest.metrics}; deviations: {m.digest.deviations or 'none'}"
        for m in ms
    ]
    planned = [
        f"{m.milestone.id} {c.id} (planned wording): {c.text}"
        for m in ms
        for c in m.milestone.acceptance_criteria
    ]
    methods = [
        f"{m.milestone.id} {m.milestone.title}\nFormulation: {m.math_spec.formulation}\n"
        f"Algorithm: {m.math_spec.algorithm}\nSignatures: {m.math_spec.signatures}\n"
        f"Tolerances: {m.math_spec.tolerances}"
        for m in ms
        if m.math_spec
    ]
    base = [
        f"{x.criterion_id} ({x.milestone_id}): {x.claim} = {x.value} {x.units}, "
        f"precision {x.precision or 'n/a'}, cite {keys[x.source_url]}"
        for x in baselines
        if x.verified and x.source_url in keys
    ]
    figs = [f"{figure_label(p)}: {p}" for p, _ in figures]
    numbers = [brief, *results, *methods, *base]
    status = f"Goal: {record.roadmap.goal}\nPartial run: {record.partial}"
    out: dict[str, tuple[str, list[str]]] = {
        "abstract": ("\n".join([status, brief, *results]), numbers),
        "introduction": (
            "\n".join([status, brief, *(f"{m.milestone.id} {m.milestone.title}" for m in ms)]),
            numbers,
        ),
        "experiments": ("\n".join([*results, *planned, *base, *figs, *methods]), numbers),
        "conclusion": ("\n".join([status, *results]), numbers),
    }
    if methods:
        out["method"] = ("\n\n".join(methods), numbers)
    if cards:
        out["related_work"] = (
            "\n".join(
                f"[{c.citation.key}] {c.citation.title} ({c.citation.year}): problem {c.problem}; "
                f"method {c.method}; results {c.results}; relevance {c.relevance}"
                for c in cards
            ),
            [],
        )
    return out
