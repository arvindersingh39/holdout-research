"""PDF text extraction with PyMuPDF: drops running headers/footers, marks headings as `##`."""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from r2r.ingest.documents import neutralise

HEADING_SIZE_RATIO = 1.15
MAX_BOLD_HEADING_CHARS = 80
MARGIN = 0.08  # header/footer candidates sit in the top or bottom 8% of the page
_BOLD_FLAG = 16


@dataclass(frozen=True)
class _Line:
    page: int
    block: int
    text: str
    size: float
    bold: bool
    in_margin: bool


def _lines(doc: pymupdf.Document) -> list[_Line]:
    out = []
    for page_no, page in enumerate(doc):
        height = page.rect.height
        for b, block in enumerate(page.get_text("dict")["blocks"]):
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = " ".join(s["text"].strip() for s in spans)
                size = max(s["size"] for s in spans)
                bold = all(s["flags"] & _BOLD_FLAG or "bold" in s["font"].lower() for s in spans)
                y0, y1 = line["bbox"][1], line["bbox"][3]
                in_margin = y0 < height * MARGIN or y1 > height * (1 - MARGIN)
                out.append(_Line(page_no, b, text, size, bold, in_margin))
    return out


def _normalise(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower().strip())


def extract_pdf(path: Path) -> str:
    """Plain text with `## ` heading lines and blank lines between paragraphs."""
    with pymupdf.open(path) as doc:
        n_pages = doc.page_count
        lines = _lines(doc)
    if not lines:
        return ""

    # Running headers/footers: the same margin line (digits ignored) on more than half the pages.
    pages_with: dict[str, set[int]] = {}
    for ln in lines:
        if not ln.in_margin:
            continue
        pages_with.setdefault(_normalise(ln.text), set()).add(ln.page)
    repeated = (
        {k for k, pages in pages_with.items() if len(pages) > n_pages / 2} if n_pages > 1 else set()
    )
    lines = [ln for ln in lines if not (ln.in_margin and _normalise(ln.text) in repeated)]
    if not lines:
        return ""

    sizes = Counter()
    for ln in lines:
        sizes[round(ln.size, 1)] += len(ln.text)
    body = statistics.median_high(sorted(sizes.elements()))

    def is_heading(ln: _Line) -> bool:
        if ln.size >= body * HEADING_SIZE_RATIO:
            return True
        return ln.bold and len(ln.text) <= MAX_BOLD_HEADING_CHARS and not ln.text.endswith(".")

    paragraphs: list[str] = []
    current: list[str] = []
    last_key: tuple[int, int] | None = None

    def flush() -> None:
        if current:
            joined = " ".join(current)
            paragraphs.append(re.sub(r"(\w)- (\w)", r"\1\2", joined))  # undo hyphenation
            current.clear()

    for ln in lines:
        if is_heading(ln):
            flush()
            paragraphs.append(f"## {ln.text}")
            last_key = None
            continue
        key = (ln.page, ln.block)
        if key != last_key:
            flush()
        current.append(ln.text)
        last_key = key
    flush()
    return neutralise("\n\n".join(paragraphs))
