"""LaTeX for the paper (Phase 8): template, escaping, bibliography and the code checks that run
before a draft reaches the owner (citation keys exist; numbers come from the inputs)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import jinja2

from r2r.contracts import BaselineCard, PaperCard
from r2r.ingest.citations import escape_latex

TEMPLATES = Path(__file__).parent / "templates"
HEADINGS = [
    ("introduction", "Introduction"),
    ("related_work", "Related work"),
    ("method", "Method"),
    ("experiments", "Experiments"),
    ("conclusion", "Conclusion"),
]
_CITE = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}")
_NOT_PROSE = re.compile(
    r"\\(?:cite[a-zA-Z]*|ref|eqref|label|url|href|includegraphics)\*?(?:\[[^\]]*\])*\{[^}]*\}"
)
_DECIMAL = re.compile(r"(?<![\d.])\d+\.\d+(?:[eE][-+]?\d+)?(?![\d.])")
_NUMBER = re.compile(r"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def escape(text: str) -> str:
    return escape_latex(text)


@dataclass(frozen=True)
class BibEntry:
    key: str
    text: str  # LaTeX, already escaped


def bib_entries(cards: Iterable[PaperCard], baselines: Iterable[BaselineCard]) -> list[BibEntry]:
    """PaperCards (keys as in refs.bib) and verified baseline sources (keys web1, web2, …)."""
    out: list[BibEntry] = []
    seen: dict[str, int] = {}
    for card in cards:
        c = card.citation
        n = seen.get(c.key, 0)
        seen[c.key] = n + 1
        key = c.key if n == 0 else f"{c.key}-{chr(ord('a') + n)}"
        parts = [", ".join(escape(a) for a in c.authors)] if c.authors else []
        parts.append(r"\emph{" + escape(c.title) + "}")
        parts += [escape(c.venue)] if c.venue else []
        parts += [str(c.year)] if c.year else []
        parts += [r"\url{" + c.url + "}"] if c.url else []
        out.append(BibEntry(key, ". ".join(parts) + "."))
    urls: dict[str, str] = {}
    for b in baselines:
        if b.verified and b.source_url not in urls:
            urls[b.source_url] = b.source_title
    for i, (url, title) in enumerate(urls.items(), 1):
        out.append(BibEntry(f"web{i}", f"{escape(title or url)}. \\url{{{url}}}."))
    return out


def baseline_keys(baselines: Iterable[BaselineCard]) -> dict[str, str]:
    """source_url -> cite key, matching `bib_entries`."""
    urls: list[str] = []
    for b in baselines:
        if b.verified and b.source_url not in urls:
            urls.append(b.source_url)
    return {u: f"web{i}" for i, u in enumerate(urls, 1)}


def bibliography(entries: list[BibEntry]) -> str:
    if not entries:
        return ""
    items = "\n".join(rf"\bibitem{{{e.key}}} {e.text}" for e in entries)
    return "\\begin{thebibliography}{99}\n" + items + "\n\\end{thebibliography}"


def check_braces(latex: str) -> None:
    """Raise ValueError if `{` and `}` don't balance (escaped braces and comments ignored)."""
    depth = 0
    for line in latex.splitlines():
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "%":
                break
            depth += {"{": 1, "}": -1}.get(ch, 0)
            if depth < 0:
                raise ValueError("unbalanced braces: a `}` closes nothing")
            i += 1
    if depth:
        raise ValueError(f"unbalanced braces: {depth} `{{` never closed")


def cited_keys(latex: str) -> set[str]:
    return {k.strip() for m in _CITE.finditer(latex) for k in m.group(1).split(",") if k.strip()}


def check_cites(latex: str, known: set[str]) -> None:
    """Raise ValueError naming unknown keys (inside the writer's repair loop)."""
    unknown = sorted(cited_keys(latex) - known)
    if unknown:
        raise ValueError(f"unknown citation keys: {', '.join(unknown)}")


def unverified_numbers(latex: str, sources: Iterable[str]) -> list[str]:
    """Decimal numbers in the text that no input states (at the text's precision). Shown to the
    owner at the publication gate; integers are treated as prose (sizes, counts, equation refs)."""
    values = [float(x) for s in sources for x in _NUMBER.findall(s)]
    out: list[str] = []
    for tok in _DECIMAL.findall(_NOT_PROSE.sub(" ", latex)):
        x = float(tok)
        mantissa = tok.split("e")[0].split("E")[0]
        decimals = len(mantissa.split(".")[1])
        half = (
            0.5
            * 10 ** (-decimals)
            * (10 ** int(tok.lower().split("e")[1]) if "e" in tok.lower() else 1)
        )
        if not any(abs(x - v) <= half + 1e-15 for v in values) and tok not in out:
            out.append(tok)
    return out


def figure_label(path: str) -> str:
    return "fig:" + re.sub(r"[^A-Za-z0-9]+", "-", Path(path).stem).strip("-")


def render_paper(
    *,
    title: str,
    authors: list[str],
    sections: Mapping[str, str],
    figures: list[tuple[str, str]],
    bib: str,
    partial: bool,
) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES),
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\VAR{",
        variable_end_string="}",
        comment_start_string=r"\#{",
        comment_end_string="}",
        line_comment_prefix="%%",
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
        undefined=jinja2.StrictUndefined,
    )
    full = {k: sections.get(k, "") for k in ("abstract", *(h[0] for h in HEADINGS))}
    return env.get_template("arxiv.tex.j2").render(
        title=escape(title),
        authors=[escape(a) for a in authors],
        sections=full,
        headings=HEADINGS,
        figures=[(p, escape(c), figure_label(p)) for p, c in figures],
        bib=bib,
        partial=partial,
    )
