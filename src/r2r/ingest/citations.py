"""Writes refs.bib from PaperCards, without an LLM."""

from __future__ import annotations

import re

from r2r.contracts import Citation, PaperCard

_SPECIALS = re.compile(r"([&%$#_{}])")
_VENUE_FIELD = {"article": "journal", "inproceedings": "booktitle", "book": "publisher",
                "techreport": "institution", "phdthesis": "school"}  # fmt: skip


def _escape(text: str) -> str:
    text = text.replace("\\", r"\textbackslash{}")
    text = _SPECIALS.sub(r"\\\1", text).replace(r"\textbackslash\{\}", r"\textbackslash{}")
    return text.replace("~", r"\textasciitilde{}").replace("^", r"\textasciicircum{}")


def _raw(text: str) -> str:
    """For doi/url: keep as-is except braces, which would break the entry."""
    return text.replace("{", "").replace("}", "")


def _entry(c: Citation, key: str) -> str:
    fields = [("title", _escape(c.title))]
    if c.authors:
        fields.append(("author", " and ".join(_escape(a) for a in c.authors)))
    if c.year is not None:
        fields.append(("year", str(c.year)))
    if c.venue:
        fields.append((_VENUE_FIELD.get(c.entry_type, "howpublished"), _escape(c.venue)))
    if c.doi:
        fields.append(("doi", _raw(c.doi)))
    if c.url:
        fields.append(("url", _raw(c.url)))
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields)
    return f"@{c.entry_type}{{{key},\n{body}\n}}\n"


def to_bibtex(cards: list[PaperCard]) -> str:
    seen: dict[str, int] = {}
    entries = []
    for card in cards:
        base = card.citation.key
        n = seen.get(base, 0)
        seen[base] = n + 1
        key = base if n == 0 else f"{base}-{chr(ord('a') + n)}"
        entries.append(_entry(card.citation, key))
    return "\n".join(entries)
