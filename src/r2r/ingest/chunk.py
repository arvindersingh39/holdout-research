"""Heading-aware chunks of ~chunk_tokens with ~overlap_tokens of overlap; no LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass

from r2r.llm.tokens import estimate_tokens

CHARS_PER_TOKEN = 4  # matches estimate_tokens' fallback
SUMMARY_CHARS = 300
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    heading: str
    text: str  # starts with "## heading" when there is one
    summary: str
    tokens: int


def _sections(text: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = [("", [])]
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if para.startswith("## "):
            sections.append((para[3:].strip(), []))
        else:
            sections[-1][1].append(para)
    return [(h, ps) for h, ps in sections if ps]


def _units(para: str, max_tokens: int) -> list[str]:
    """A paragraph, or its sentences, or hard slices: each fits `max_tokens`."""
    if estimate_tokens(para) <= max_tokens:
        return [para]
    out: list[str] = []
    width = max_tokens * CHARS_PER_TOKEN
    for sentence in _SENTENCE_END.split(para):
        if estimate_tokens(sentence) <= max_tokens:
            out.append(sentence)
        else:
            out += [sentence[i : i + width] for i in range(0, len(sentence), width)]
    return out


def _tail(text: str, tokens: int) -> str:
    if tokens <= 0:
        return ""
    tail = text[-tokens * CHARS_PER_TOKEN :]
    if " " in tail and len(tail) < len(text):
        tail = tail[tail.index(" ") + 1 :]  # start at a word boundary
    return tail


def _first_sentence(text: str) -> str:
    return _SENTENCE_END.split(text.strip(), maxsplit=1)[0]


def _make(heading: str, body: str, fresh: str) -> Chunk:
    text = f"## {heading}\n\n{body}" if heading else body
    first = _first_sentence(fresh)
    summary = (f"{heading}: {first}" if heading else first)[:SUMMARY_CHARS]
    return Chunk(heading=heading, text=text, summary=summary, tokens=estimate_tokens(text))


def chunk_text(text: str, chunk_tokens: int = 800, overlap_tokens: int = 100) -> list[Chunk]:
    chunks: list[Chunk] = []
    for heading, paras in _sections(text):
        units = [u for p in paras for u in _units(p, chunk_tokens)]
        overlap = ""
        current: list[str] = []
        for unit in units:
            candidate = "\n\n".join([*current, unit])
            if current and estimate_tokens(candidate) > chunk_tokens:
                body = "\n\n".join(current)
                fresh = body[len(overlap) :].lstrip() if overlap else body
                chunks.append(_make(heading, body, fresh))
                overlap = _tail(body, overlap_tokens)
                current = [overlap] if overlap else []
            current.append(unit)
        if current and (len(current) > 1 or current[0] != overlap):
            body = "\n\n".join(current)
            fresh = body[len(overlap) :].lstrip() if overlap else body
            chunks.append(_make(heading, body, fresh))
    return chunks
