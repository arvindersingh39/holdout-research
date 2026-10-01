"""Heading-aware chunking with overlap."""

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from r2r.ingest.chunk import chunk_text
from r2r.llm.tokens import estimate_tokens


def _doc(sections: int = 3, paras: int = 12, words: int = 60) -> str:
    out = []
    for s in range(sections):
        out.append(f"## Section {s}")
        for p in range(paras):
            out.append(" ".join(f"s{s}p{p}w{w}." if w % 12 == 11 else f"s{s}p{p}w{w}"
                                for w in range(words)))  # fmt: skip
    return "\n\n".join(out)


def test_sizes_and_headings() -> None:
    chunks = chunk_text(_doc(), chunk_tokens=200, overlap_tokens=30)
    assert len(chunks) > 3
    for c in chunks:
        assert c.tokens == estimate_tokens(c.text)
        assert c.tokens <= 200 + 30 + 20  # target + overlap + heading slack
        assert c.heading.startswith("Section ")
        assert c.text.startswith(f"## {c.heading}\n")
        assert 0 < len(c.summary) <= 300
        assert c.summary.startswith(c.heading)


def test_chunks_never_span_sections() -> None:
    for c in chunk_text(_doc(), chunk_tokens=200, overlap_tokens=30):
        n = c.heading.split()[-1]
        body_sections = set(re.findall(r"\bs(\d)p", c.text))
        assert body_sections <= {n}


def test_overlap_between_neighbours() -> None:
    chunks = [c for c in chunk_text(_doc(), 200, 30) if c.heading == "Section 0"]
    assert len(chunks) >= 2
    tail = chunks[0].text.split()[-5:]
    assert " ".join(tail) in chunks[1].text


def test_short_text_one_chunk_and_preamble() -> None:
    chunks = chunk_text("Intro words here.\n\n## A\n\nShort body.", 800, 100)
    assert [c.heading for c in chunks] == ["", "A"]
    assert chunks[0].summary == "Intro words here."


def test_overlong_sentence_is_split() -> None:
    chunks = chunk_text("## H\n\n" + "x" * 5000, chunk_tokens=100, overlap_tokens=0)
    assert all(c.tokens <= 120 for c in chunks)
    assert "".join(c.text.removeprefix("## H\n\n") for c in chunks) == "x" * 5000


def test_empty_text() -> None:
    assert chunk_text("   \n\n ", 800, 100) == []


@settings(max_examples=40, deadline=None)
@given(
    st.lists(st.lists(st.from_regex(r"[a-z]{1,8}", fullmatch=True), min_size=1, max_size=40),
             min_size=1, max_size=30),
    st.integers(min_value=20, max_value=300),
)  # fmt: skip
def test_every_paragraph_is_covered(paras: list[list[str]], target: int) -> None:
    text = "\n\n".join(" ".join(words) + "." for words in paras)
    joined = "\n".join(c.text for c in chunk_text(text, target, target // 5))
    for words in paras:
        para = " ".join(words) + "."
        if estimate_tokens(para) <= target:
            assert para in joined
